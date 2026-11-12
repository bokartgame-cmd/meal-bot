"""MAX-бот: обработчики для студентов (регистрация с паролем, QR-карточка)"""
import logging
from aiogram import BaseMiddleware, Router, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from bot.fsm import StudentRegistration
from bot.keyboards import (
    CB_CANCEL,
    CB_HELP,
    CB_RESTART_REG,
    CB_STUDENT_QR,
    STUDENT_HELP_TEXT,
    STUDENT_INACTIVE_TEXT,
    cancel_menu,
    get_qr_menu_inline,
    get_restart_reg_inline,
    get_start_menu_inline,
)
from core.qr import generate_diplink, generate_qr_card_bytes
from core.archive import is_user_archived
from core.services import get_active_code, get_user_info, save_user_chat_id, verify_student

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

# Глобальный словарь для хранения ID сообщений студентов
# Структура: {user_id: {"qr_message_id": int, "received_message_id": int}}
student_messages = {}

REGISTRATION_DENIED_TEXTS = {
    "not_in_list": (
        "❌ Данные не найдены в списке разрешённых получателей.\n"
        "Пожалуйста, обратитесь в деканат для уточнения информации."
    ),
    "deactivated": STUDENT_INACTIVE_TEXT,
    "already_registered": (
        "⚠️ Эти данные уже привязаны к другому аккаунту.\n"
        "Повторная регистрация по чужим данным невозможна.\n"
        "Если это ошибка — обратитесь к администратору."
    ),
    "wrong_password": (
        "❌ Неверный пароль.\n"
        "Проверьте введённые данные и попробуйте ещё раз."
    ),
}


def is_inactive_student(info) -> bool:
    """Студент, исключённый из текущего списка получателей"""
    return bool(info) and info.get("role") == "student" and not info.get("is_active")


@router.message(CommandStart(deep_link=False))
async def cmd_start(message: Message, state: FSMContext):
    """Точка входа: регистрация, QR-карточка студента или меню сотрудника"""
    await state.clear()
    info = await get_user_info(message.from_user.id)

    # Студент в архиве (практика, академ): аккаунт перенесён, регистрироваться заново не нужно
    if not info and await is_user_archived(message.from_user.id):
        await message.answer(STUDENT_INACTIVE_TEXT)
        return

    # Незарегистрированный -> регистрация
    if not info or not info.get("is_verified"):
        await message.answer(
            "👋 Добро пожаловать в бот питания!\n\n"
            "Для доступа к функциям необходимо пройти регистрацию.\n"
            "Введите ваше ФИО полностью (как в списке деканата):",
            reply_markup=cancel_menu(),
        )
        await state.set_state(StudentRegistration.full_name)
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

    # Студент -> сразу карточка с QR, без сообщения "Выберите действие:"
    await send_qr_card(message, message.from_user.id)


# --- Регистрация (FSM): ФИО -> группа -> пароль ---
@router.callback_query(F.data == CB_RESTART_REG)
async def cb_restart_registration(callback: CallbackQuery, state: FSMContext):
    """Кнопка 'Начать заново' под сообщением об ошибке регистрации"""
    await state.clear()
    await callback.message.edit_text(
        "Введите ваше ФИО полностью (как в списке деканата):",
        reply_markup=cancel_menu(),
    )
    await state.set_state(StudentRegistration.full_name)
    await callback.answer()


@router.callback_query(
    StateFilter(StudentRegistration.full_name, StudentRegistration.group_number, StudentRegistration.password),
    F.data == CB_CANCEL,
)
async def reg_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена регистрации через inline-кнопку"""
    await state.clear()
    await callback.message.edit_text("Регистрация отменена.", reply_markup=None)
    await callback.answer()


@router.message(StudentRegistration.full_name, F.text)
async def process_full_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text.strip())
    await message.answer(
        "Введите номер вашей группы (например, 28-143):",
        reply_markup=cancel_menu(),
    )
    await state.set_state(StudentRegistration.group_number)


@router.message(StudentRegistration.group_number, F.text)
async def process_group_number(message: Message, state: FSMContext):
    await state.update_data(group_number=message.text.strip())
    await message.answer(
        "Введите ваш личный пароль (6 символов, выдан вместе со списком):",
        reply_markup=cancel_menu(),
    )
    await state.set_state(StudentRegistration.password)


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
        # Просто карточка с QR, без сообщения "Выберите действие:"
        await send_qr_card(message, message.from_user.id)
    else:
        text = REGISTRATION_DENIED_TEXTS.get(
            result.get("reason"),
            "❌ Не удалось завершить регистрацию. Попробуйте ещё раз.",
        )
        await message.answer(text, reply_markup=get_restart_reg_inline())


# --- Кнопки под QR-карточкой ---
@router.callback_query(F.data == CB_STUDENT_QR)
async def cb_student_qr(callback: CallbackQuery):
    """Обновить QR: старое сообщение удаляется, приходит новая карточка"""
    await callback.answer()

    info = await get_user_info(callback.from_user.id)
    if not info or not info.get("is_verified"):
        await callback.message.edit_text("❌ Сначала пройдите регистрацию (/start).")
        return
    if is_inactive_student(info):
        await callback.message.edit_text(STUDENT_INACTIVE_TEXT)
        return

    # Удаляем старую карточку (если сообщение слишком старое — просто шлём новую)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить старое сообщение с QR: %s", e)

    await send_qr_card(callback.message, callback.from_user.id)


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
    username = message.from_user.username or "не указан"
    await message.answer(
        f"🆔 Ваш ID в MAX: {user_id}\n"
        f"👤 Username: @{username}"
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
            # Студент — сразу QR
            await send_qr_card(message, message.from_user.id)
        return

    await state.set_state(StudentRegistration.full_name)
    await process_full_name(message, state)


# --- Общее: карточка с QR и буквенным кодом ---
async def send_qr_card(message: Message, user_id: int):
    """Карточка: QR + крупный код; под сообщением кнопки [Обновить QR] и [Инструкция]"""
    code = await get_active_code(user_id)
    if not code:
        await message.answer("❌ Ошибка: код не найден. Пройдите регистрацию заново.")
        return

    # Удаляем старое сообщение с QR, если есть
    if user_id in student_messages and "qr_message_id" in student_messages[user_id]:
        try:
            await message.bot.delete_message(
                chat_id=message.chat.id,
                message_id=student_messages[user_id]["qr_message_id"]
            )
        except Exception as e:
            logger.warning("Не удалось удалить старое QR-сообщение: %s", e)

    diplink = generate_diplink(code, is_max=True)
    qr_buf = generate_qr_card_bytes(diplink, code)
    qr_file = BufferedInputFile(qr_buf.read(), filename=f"qr_{code}.png")
    caption = (
        f"✅ Ваш код на сегодня: {code}\n"
        f"Покажите этот QR-код сотруднику столовой.\n"
        f"⚠️ Код обновляется каждый день, вчерашний QR не сработает."
    )

    sent_message = await message.answer_photo(
        photo=qr_file,
        caption=caption,
        reply_markup=get_qr_menu_inline(),
    )

    # Сохраняем ID нового сообщения
    if user_id not in student_messages:
        student_messages[user_id] = {}
    student_messages[user_id]["qr_message_id"] = sent_message.message_id

