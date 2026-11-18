"""MAX-бот: обработчики для студентов (регистрация с паролем, главное меню)"""
import asyncio
import logging
from aiogram import BaseMiddleware, Router, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from bot.fsm import StudentRegistration
from bot.keyboards import (
    CB_CANCEL,
    CB_HELP,
    CB_REG_BACK,
    CB_RESTART_REG,
    STUDENT_HELP_TEXT,
    STUDENT_INACTIVE_TEXT,
    cancel_menu,
    reg_step_menu,
    get_restart_reg_inline,
    get_start_menu_inline,
)
from core.archive import is_user_archived
from core.meals import get_rights
from core.services import get_user_info, save_user_chat_id, verify_student

logger = logging.getLogger(__name__)
router = Router(name="max_user")


class RememberChatMiddleware(BaseMiddleware):
    """Запоминает chat_id личного диалога пользователя: он нужен для уведомлений.

    Сохраняем ПОСЛЕ обработчика, чтобы при регистрации запись в users уже существовала.
    """

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        finally:
            try:
                msg = event if isinstance(event, Message) else getattr(event, "message", None)
                chat = getattr(msg, "chat", None)
                user = getattr(event, "from_user", None)
                if chat and user:
                    await save_user_chat_id(user.id, chat.id)
            except Exception as e:
                logger.debug("Не удалось сохранить chat_id: %s", e)


router.message.outer_middleware(RememberChatMiddleware())
router.callback_query.outer_middleware(RememberChatMiddleware())

REGISTRATION_DENIED_TEXTS = {
    "not_in_list": (
        "❌ Данные не найдены в списке разрешённых получателей.\n"
        "Пожалуйста, обратитесь в деканат для уточнения информации."
    ),
    "deactivated": STUDENT_INACTIVE_TEXT,
    "already_registered": (
        "⚠️ Эти данные уже привязаны к другому аккаунту.\n"
        "Повторная регистрация по чужим данным невозможна.\n"
        "Если это ошибка — обратитесь к куратору."
    ),
    "wrong_password": (
        "❌ Неверный пароль.\n"
        "Проверьте введённые данные и попробуйте ещё раз."
    ),
}


def is_inactive_student(info) -> bool:
    """Студент, исключённый из текущего списка получателей"""
    return bool(info) and info.get("role") == "student" and not info.get("is_active")


REG_MSGS_KEY = "reg_msg_ids"
REG_CANCELLED_TEXT = "Регистрация отменена, вы можете начать заново."
REG_PROMPT_FULL_NAME = "Введите ваше ФИО полностью:"
REG_PROMPT_GROUP = "Введите номер вашей группы (например, 16-534):"
REG_PROMPT_PASSWORD = "Введите ваш личный пароль (6 символов, выдается куратором):"


async def _track(state: FSMContext, *items):
    """Запоминаем сообщения шагов регистрации (объекты Message), чтобы удалить их при отмене"""
    data = await state.get_data()
    ids = list(data.get(REG_MSGS_KEY, []))
    for item in items:
        mid = getattr(item, "message_id", None)
        if mid is not None and mid not in ids:
            ids.append(mid)
    await state.update_data(**{REG_MSGS_KEY: ids})


async def _delete_by_id(bot, chat_id, message_id):
    """Удаление сообщения по id; ошибки (уже удалено, нет прав) не должны ломать отмену"""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug("Не удалось удалить сообщение %s: %s", message_id, e)


@router.message(CommandStart(deep_link=False))
async def cmd_start(message: Message, state: FSMContext):
    """Точка входа: регистрация, главное меню студента или меню сотрудника"""
    await state.clear()
    info = await get_user_info(message.from_user.id)

    # Студент в архиве (практика, академ): аккаунт перенесён, регистрироваться заново не нужно
    if not info and await is_user_archived(message.from_user.id):
        await message.answer(STUDENT_INACTIVE_TEXT)
        return

    # Незарегистрированный -> регистрация
    if not info or not info.get("is_verified"):
        sent = await message.answer(
            "👋 Добро пожаловать в бот питания!\n\n"
            "Для доступа к функциям необходимо пройти регистрацию.\n"
            "Введите ваше ФИО полностью:",
            reply_markup=cancel_menu(),
        )
        await state.set_state(StudentRegistration.full_name)
        await _track(state, message, sent)
        return

    # 🔒 Неактивный студент — только сообщение-предупреждение
    if is_inactive_student(info):
        await message.answer(STUDENT_INACTIVE_TEXT)
        return

    # Сотрудник/админ -> меню (не трогаем)
    if info["role"] in ("staff", "admin"):
        await message.answer(
            "👋 С возвращением! Выберите действие:",
            reply_markup=get_start_menu_inline(info["role"]),
        )
        return

    # Студент -> главное меню с числом прав на сегодня
    await send_student_home(message, message.from_user.id)


# --- Регистрация (FSM): ФИО -> группа -> пароль ---
@router.callback_query(F.data == CB_RESTART_REG)
async def cb_restart_registration(callback: CallbackQuery, state: FSMContext):
    """Кнопка 'Начать заново' под сообщением об ошибке регистрации"""
    await state.clear()
    await callback.message.edit_text(
        REG_PROMPT_FULL_NAME,
        reply_markup=cancel_menu(),
    )
    await state.set_state(StudentRegistration.full_name)
    await _track(state, callback.message)
    await callback.answer()


@router.callback_query(
    StateFilter(StudentRegistration.full_name, StudentRegistration.group_number, StudentRegistration.password),
    F.data == CB_CANCEL,
)
async def reg_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена регистрации: удаляем все сообщения шагов (и бота, и студента), присылаем итог с кнопкой"""
    data = await state.get_data()
    ids = list(data.get(REG_MSGS_KEY, []))
    await state.clear()
    cur_id = getattr(callback.message, "message_id", None)
    if cur_id is not None and cur_id not in ids:
        ids.append(cur_id)
    chat_id = callback.message.chat.id
    await asyncio.gather(*(_delete_by_id(callback.bot, chat_id, mid) for mid in ids))
    await callback.message.answer(REG_CANCELLED_TEXT, reply_markup=get_restart_reg_inline())
    await callback.answer()


@router.callback_query(
    StateFilter(StudentRegistration.group_number, StudentRegistration.password),
    F.data == CB_REG_BACK,
)
async def reg_back(callback: CallbackQuery, state: FSMContext):
    """Шаг назад: удаляем текущий вопрос, ответ студента и предыдущий вопрос,
    затем отправляем предыдущий вопрос заново (внизу чата) и ждём ввод"""
    current = await state.get_state()
    if current == StudentRegistration.group_number.state:
        previous, text, markup = StudentRegistration.full_name, REG_PROMPT_FULL_NAME, cancel_menu()
    else:
        previous, text, markup = StudentRegistration.group_number, REG_PROMPT_GROUP, reg_step_menu()
    data = await state.get_data()
    ids = list(data.get(REG_MSGS_KEY, []))
    cur_id = getattr(callback.message, "message_id", None)
    # Хвост списка: [..., предыдущий вопрос бота, ответ студента на него, текущий вопрос бота]
    if cur_id in ids:
        start = max(ids.index(cur_id) - 2, 0)
        to_delete, ids = ids[start:], ids[:start]
    else:
        to_delete = [cur_id] if cur_id is not None else []
    await state.set_state(previous)
    await state.update_data(**{REG_MSGS_KEY: ids})
    chat_id = callback.message.chat.id
    await asyncio.gather(*(_delete_by_id(callback.bot, chat_id, mid) for mid in to_delete))
    sent = await callback.message.answer(text, reply_markup=markup)
    await _track(state, sent)
    await callback.answer()


@router.message(StudentRegistration.full_name, F.text)
async def process_full_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text.strip())
    sent = await message.answer(
        REG_PROMPT_GROUP,
        reply_markup=reg_step_menu(),
    )
    await state.set_state(StudentRegistration.group_number)
    await _track(state, message, sent)


@router.message(StudentRegistration.group_number, F.text)
async def process_group_number(message: Message, state: FSMContext):
    await state.update_data(group_number=message.text.strip())
    sent = await message.answer(
        REG_PROMPT_PASSWORD,
        reply_markup=reg_step_menu(),
    )
    await state.set_state(StudentRegistration.password)
    await _track(state, message, sent)


@router.message(StudentRegistration.password, F.text)
async def process_password(message: Message, state: FSMContext):
    """Финальная проверка: ФИО + группа + пароль + актуальность в списке"""
    data = await state.get_data()
    result = await verify_student(
        message.from_user.id,
        data["full_name"],
        data["group_number"],
        message.text,
    )
    await state.clear()

    if result["status"] == "verified":
        # Регистрация завершена: главное меню с правами на сегодня
        await send_student_home(message, message.from_user.id)
    else:
        text = REGISTRATION_DENIED_TEXTS.get(
            result.get("reason"),
            "❌ Не удалось завершить регистрацию. Попробуйте ещё раз.",
        )
        await message.answer(text, reply_markup=get_restart_reg_inline())


@router.callback_query(F.data == CB_HELP)
async def cb_help(callback: CallbackQuery):
    await callback.answer()
    info = await get_user_info(callback.from_user.id)
    if info and info.get("role") in ("staff", "admin"):
        from bot.keyboards import STAFF_HELP_TEXT
        text = STAFF_HELP_TEXT
    else:
        text = STUDENT_HELP_TEXT
    await callback.message.answer(text)


# --- Отладочная slash-команда ---
@router.message(Command("id"))
async def cmd_get_id(message: Message):
    """Показывает пользователю его ID"""
    user_id = message.from_user.id
    await message.answer(
        f"🆔 Ваш ID в MAX: {user_id}\n"
        f"Отправьте этот ID администратору, для получения роли."

    )


# --- Страховка: текст БЕЗ состояния FSM (например, после перезапуска бота) ---
@router.message(StateFilter(None), F.text)
async def handle_text_without_state(message: Message, state: FSMContext):
    """Бот не молчит: либо возвращаем меню, либо возобновляем регистрацию"""
    logger.info("Текст без FSM-состояния: %r", message.text)
    info = await get_user_info(message.from_user.id)

    if info and info.get("is_verified"):
        if is_inactive_student(info):
            await message.answer(STUDENT_INACTIVE_TEXT)
        elif info["role"] in ("staff", "admin"):
            await message.answer(
                "👋 С возвращением! Выберите действие:",
                reply_markup=get_start_menu_inline(info["role"]),
            )
        else:
            # Студент — главное меню
            await send_student_home(message, message.from_user.id)
        return

    await state.set_state(StudentRegistration.full_name)
    await process_full_name(message, state)


# --- Главное меню студента (кнопка «Получить обед») ---
async def send_student_home(message: Message, user_id: int):
    """Сколько прав на обед у студента сегодня и что делать дальше"""
    rights = await get_rights(user_id)
    if rights is None:
        await message.answer(STUDENT_INACTIVE_TEXT)
        return
    if rights > 0:
        status = f"🍽 Права на обед сегодня: {rights}."
    else:
        status = "🍽 На сегодня права на обед нет: оно уже использовано или подарено."
    await message.answer(
        f"{status}\n\n"
        "Чтобы получить обед, нажмите «Получить обед» у раздачи и подтвердите запрос. "
        "Право можно подарить другу кнопкой «Угостить друга». Неиспользованное право сгорает в конце суток.",
        reply_markup=get_start_menu_inline("student"),
    )
