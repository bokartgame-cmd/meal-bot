"""MAX-бот: Обработчики для сотрудников столовой"""
import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
import config
from bot.fsm import StaffManualCode
from bot.keyboards import (
    BTN_CANCEL,
    CB_CANCEL,
    CB_STAFF_CODE,
    STUDENT_MEAL_RECEIVED_TEXT,
    STUDENT_RATE_PROMPT,
    get_start_menu_inline,
    get_cancel_inline,
    get_rating_inline,
)
from core.max_api import send_text_to_user
from core.services import get_user_info, process_issuance

logger = logging.getLogger(__name__)
router = Router(name="max_staff")

async def notify_student(message: Message, result: dict):
    """Уведомление студента об успешной выдаче"""
    if result.get("status") != "success":
        return

    student_id = result.get("student_user_id")
    logger.info(f"Попытка уведомления студента. student_user_id={student_id} (type: {type(student_id)})")

    if not student_id:
        logger.warning("Не удалось получить student_user_id из результата выдачи. Проверьте core/services.py")
        return

    try:
        # student_id — это user_id, а не chat_id: см. core/max_api.py
        text, markup = STUDENT_MEAL_RECEIVED_TEXT, None
        meal_id = result.get("meal_id")
        if meal_id:  # просим оценить получение обеда (кнопки 1–5)
            text = f"{STUDENT_MEAL_RECEIVED_TEXT}\n\n{STUDENT_RATE_PROMPT}"
            markup = get_rating_inline(int(meal_id))
        await send_text_to_user(message.bot, int(student_id), text, markup)
        logger.info(f"Уведомление успешно отправлено студенту {student_id}")
    except Exception as e:
        # Ловим ВСЕ исключения, чтобы не прерывать работу бота для сотрудника
        logger.error(f"Ошибка при отправке уведомления студенту {student_id}: {type(e).__name__} - {e}")

@router.message(CommandStart(deep_link=True))
async def process_qr_scan(message: Message, command: CommandObject):
    """Запасной режим (пилот): сканирование личного QR студента камерой: /start CODE.
    Общий QR и подарки обрабатывает bot/routers/meal.py (он подключён раньше)."""
    if not config.LEGACY_QR_ENABLED:
        await message.answer("ℹ️ Личные QR больше не используются. Студенты сканируют общий QR на раздаче, "
                             "а вы подтверждаете выдачу кнопкой в чате.")
        return
    code = (command.args or "").strip()
    if not code:
        await message.answer("❌ Некорректный QR-код")
        return

    info = await get_user_info(message.from_user.id)
    if not info or info["role"] not in ("staff", "admin"):
        await message.answer("⛔ У вас нет прав для выдачи питания. Обратитесь к администратору.")
        return

    result = await process_issuance(code, message.from_user.id, method="qr")
    await message.answer(result["message"], reply_markup=get_start_menu_inline(info["role"]))

    # Уведомляем студента
    await notify_student(message, result)

@router.callback_query(F.data == CB_STAFF_CODE)
async def cb_enter_code(callback: CallbackQuery, state: FSMContext):
    """Кнопка меню: переход в режим ожидания кода (запасной режим пилота)"""
    if not config.LEGACY_QR_ENABLED:
        await callback.answer("Ручной ввод кода отключён.", show_alert=True)
        return
    info = await get_user_info(callback.from_user.id)
    if not info or info["role"] not in ("staff", "admin"):
        await callback.answer("⛔ У вас нет прав для выдачи питания.", show_alert=True)
        return

    await callback.message.edit_text(
        "Пришлите код питания студента текстом в чат (например, K7M2PX).",
        reply_markup=get_cancel_inline()
    )
    await state.set_state(StaffManualCode.waiting_code)
    await callback.answer()

@router.callback_query(F.data == CB_CANCEL, StaffManualCode.waiting_code)
async def code_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена ввода кода"""
    await state.clear()
    info = await get_user_info(callback.from_user.id)
    await callback.message.edit_text(
        "Действие отменено.",
        reply_markup=get_start_menu_inline(info["role"] if info else "student")
    )
    await callback.answer()

@router.message(StaffManualCode.waiting_code, F.text)
async def code_process(message: Message, state: FSMContext):
    """Обработка введенного текстом кода"""
    await state.clear()
    code = message.text.strip().upper()

    info = await get_user_info(message.from_user.id)
    result = await process_issuance(code, message.from_user.id, method="manual")

    await message.answer(result["message"], reply_markup=get_start_menu_inline(info["role"] if info else "student"))

    # Уведомляем студента
    await notify_student(message, result)