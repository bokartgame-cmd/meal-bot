"""MAX-бот: жалобы и предложения, оценка получения обеда, просмотр обращений администратором"""
import asyncio
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
from bot.fsm import StudentFeedback
from bot.keyboards import (
    CB_ADMIN_FEEDBACK,
    CB_FEEDBACK,
    CB_FEEDBACK_ADMIN_BACK,
    CB_FEEDBACK_CANCEL,
    CB_FEEDBACK_COMPLAINT,
    CB_FEEDBACK_MARK_READ,
    CB_FEEDBACK_SUGGESTION,
    CB_RATE_PREFIX,
    ROLE_ADMIN,
    STUDENT_FEEDBACK_INTRO,
    STUDENT_INACTIVE_TEXT,
    STUDENT_MEAL_RECEIVED_TEXT,
    get_after_rating_inline,
    get_feedback_admin_inline,
    get_feedback_cancel_inline,
    get_feedback_kind_inline,
    get_qr_menu_inline,
    get_start_menu_inline,
)
from core.feedback import (
    KIND_COMPLAINT,
    KIND_ICONS,
    KIND_LABELS,
    KIND_SUGGESTION,
    add_feedback,
    count_feedback_today,
    count_unread,
    get_admin_ids,
    get_recent_feedback,
    mark_all_read,
    neutralize,
    save_rating,
)
from core.max_api import send_text_to_user
from core.services import get_user_info

logger = logging.getLogger(__name__)
router = Router(name="max_feedback")

FEEDBACK_PAGE_SIZE = 10
LIST_ITEM_MAX_CHARS = 300   # в списке обращение обрезается, полный текст приходит сразу при отправке
LIST_TOTAL_MAX_CHARS = 3500  # лимит MAX на длину сообщения — 4000 символов


async def _delete_quietly(message: Message):
    try:
        await message.delete()
    except Exception:
        pass


# ====================
# ОЦЕНКА ПОЛУЧЕНИЯ ОБЕДА
# ====================

@router.callback_query(F.data.startswith(CB_RATE_PREFIX))
async def cb_rate(callback: CallbackQuery):
    """Студент оценил получение обеда: сохраняем и показываем кнопку жалоб и предложений"""
    await callback.answer()
    try:
        _, meal_id, rating = callback.data.split(":")
        meal_id, rating = int(meal_id), int(rating)
    except ValueError:
        return

    if not await save_rating(meal_id, callback.from_user.id, rating):
        await callback.message.answer("⚠️ Не удалось сохранить оценку.")
        return

    text = (
        f"{STUDENT_MEAL_RECEIVED_TEXT}\n\n"
        f"Ваша оценка: {'⭐' * rating} Спасибо!\n"
        f"Есть жалоба или предложение? Напишите нам."
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_after_rating_inline())
    except Exception as e:
        logger.warning("Не удалось обновить сообщение с оценкой: %s", e)
        await callback.message.answer(text, reply_markup=get_after_rating_inline())


# ====================
# ЖАЛОБЫ И ПРЕДЛОЖЕНИЯ (студент)
# ====================

@router.callback_query(F.data == CB_FEEDBACK)
async def cb_feedback(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    info = await get_user_info(user_id)
    if not info or not info.get("is_verified"):
        await callback.message.answer("❌ Сначала пройдите регистрацию (/start).")
        return
    if info.get("role") == "student" and not info.get("is_active"):
        await callback.message.answer(STUDENT_INACTIVE_TEXT)
        return
    if await count_feedback_today(user_id) >= config.FEEDBACK_DAILY_LIMIT:
        await callback.message.answer(
            f"⏳ Сегодня вы уже отправили максимум обращений ({config.FEEDBACK_DAILY_LIMIT}). "
            f"Попробуйте завтра."
        )
        return
    await callback.message.answer(STUDENT_FEEDBACK_INTRO, reply_markup=get_feedback_kind_inline())


@router.callback_query(F.data.in_({CB_FEEDBACK_COMPLAINT, CB_FEEDBACK_SUGGESTION}))
async def cb_feedback_kind(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    kind = KIND_COMPLAINT if callback.data == CB_FEEDBACK_COMPLAINT else KIND_SUGGESTION
    await state.set_state(StudentFeedback.waiting_text)
    await state.update_data(kind=kind)
    await _delete_quietly(callback.message)
    await callback.message.answer(
        f"{KIND_ICONS[kind]} {KIND_LABELS[kind]}\n\n"
        f"Напишите текст одним сообщением (до {config.FEEDBACK_MAX_LEN} символов).",
        reply_markup=get_feedback_cancel_inline(),
    )


@router.callback_query(F.data == CB_FEEDBACK_CANCEL)
async def cb_feedback_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await _delete_quietly(callback.message)
    await callback.message.answer("Отправка отменена.", reply_markup=get_qr_menu_inline())


@router.message(StudentFeedback.waiting_text, F.text, ~F.text.startswith("/"))
async def process_feedback_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        return
    if len(text) > config.FEEDBACK_MAX_LEN:
        await message.answer(
            f"✂️ Слишком длинно: {len(text)} из {config.FEEDBACK_MAX_LEN} символов. "
            f"Сократите текст и отправьте снова.",
            reply_markup=get_feedback_cancel_inline(),
        )
        return

    kind = (await state.get_data()).get("kind")
    if kind not in KIND_LABELS:
        await state.clear()
        await message.answer("⚠️ Что-то пошло не так. Нажмите «💬 Жалобы и предложения» и начните заново.")
        return

    user_id = message.from_user.id
    if await count_feedback_today(user_id) >= config.FEEDBACK_DAILY_LIMIT:
        await state.clear()
        await message.answer(
            f"⏳ Сегодня вы уже отправили максимум обращений ({config.FEEDBACK_DAILY_LIMIT}). "
            f"Попробуйте завтра."
        )
        return

    feedback_id = await add_feedback(user_id, kind, text)
    await state.clear()
    info = await get_user_info(user_id)
    logger.info("Обращение №%s (%s) от %s", feedback_id, kind, user_id)

    # Ответ студенту и уведомление администраторов — независимые запросы, шлём одновременно
    await asyncio.gather(
        message.answer(
            f"✅ Спасибо! Ваше обращение №{feedback_id} отправлено администратору.",
            reply_markup=get_qr_menu_inline(),
        ),
        _notify_admins(message, feedback_id, kind, info, text),
    )


@router.message(StudentFeedback.waiting_text, ~F.text)
async def process_feedback_not_text(message: Message):
    await message.answer(
        "Отправьте обращение текстом или нажмите «Отмена».",
        reply_markup=get_feedback_cancel_inline(),
    )


async def _notify_admins(message: Message, feedback_id: int, kind: str, info: dict | None, text: str):
    """Новое обращение сразу приходит всем администраторам. Ошибки отправки не прерывают работу."""
    name = ((info or {}).get("full_name") or "неизвестный").title()
    group = ((info or {}).get("group_number") or "—").upper()
    title = "Новая жалоба" if kind == KIND_COMPLAINT else "Новое предложение"
    body = f"{KIND_ICONS[kind]} {title} №{feedback_id}\nОт: {name} ({group})\n\n{neutralize(text)}"
    try:
        admin_ids = await get_admin_ids()
        results = await asyncio.gather(
            *(send_text_to_user(message.bot, admin_id, body) for admin_id in admin_ids),
            return_exceptions=True,
        )
        for admin_id, res in zip(admin_ids, results):
            if isinstance(res, Exception):
                logger.warning("Не удалось уведомить администратора %s об обращении №%s: %s: %s",
                               admin_id, feedback_id, type(res).__name__, res)
    except Exception:
        logger.exception("Ошибка при уведомлении администраторов об обращении №%s", feedback_id)


# ====================
# ОБРАЩЕНИЯ ДЛЯ АДМИНИСТРАТОРА
# ====================

async def _require_admin(target, user_id: int) -> bool:
    info = await get_user_info(user_id)
    if info and info.get("role") == ROLE_ADMIN:
        return True
    if isinstance(target, CallbackQuery):
        await target.answer("⛔ Только для администратора.", show_alert=True)
    else:
        await target.answer("⛔ Только для администратора.")
    return False


def _format_feedback_list(items: list, unread: int) -> str:
    if not items:
        return "📬 Обращений студентов пока нет."
    lines = [f"📬 Обращения студентов\nНепрочитанных: {unread}\nПоследние {len(items)}:\n"]
    total = len(lines[0])
    for it in items:
        body = neutralize(it["text"])
        if len(body) > LIST_ITEM_MAX_CHARS:
            body = body[:LIST_ITEM_MAX_CHARS].rstrip() + "…"
        mark = "🆕" if not it["is_read"] else "▫️"
        entry = (
            f"{mark} №{it['id']} · {KIND_ICONS.get(it['kind'], '')} {KIND_LABELS.get(it['kind'], it['kind'])} · "
            f"{str(it['created_at'])[:16]}\n"
            f"{it['full_name'].title()} ({str(it['group_number']).upper()})\n{body}\n"
        )
        if total + len(entry) > LIST_TOTAL_MAX_CHARS:
            lines.append("… остальные не поместились в сообщение.")
            break
        lines.append(entry)
        total += len(entry)
    return "\n".join(lines)


async def _show_feedback(target: Message):
    items = await get_recent_feedback(FEEDBACK_PAGE_SIZE)
    unread = await count_unread()
    await target.answer(_format_feedback_list(items, unread), reply_markup=get_feedback_admin_inline(unread))


@router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext):
    if not await _require_admin(message, message.from_user.id):
        return
    await state.clear()
    await _show_feedback(message)


@router.callback_query(F.data == CB_ADMIN_FEEDBACK)
async def cb_admin_feedback(callback: CallbackQuery, state: FSMContext):
    if not await _require_admin(callback, callback.from_user.id):
        return
    await callback.answer()
    await state.clear()
    await _delete_quietly(callback.message)
    await _show_feedback(callback.message)


@router.callback_query(F.data == CB_FEEDBACK_MARK_READ)
async def cb_feedback_mark_read(callback: CallbackQuery):
    if not await _require_admin(callback, callback.from_user.id):
        return
    await callback.answer()
    await mark_all_read()
    await _delete_quietly(callback.message)
    await _show_feedback(callback.message)


@router.callback_query(F.data == CB_FEEDBACK_ADMIN_BACK)
async def cb_feedback_admin_back(callback: CallbackQuery):
    if not await _require_admin(callback, callback.from_user.id):
        return
    await callback.answer()
    await _delete_quietly(callback.message)
    await callback.message.answer("👋 Выберите действие:", reply_markup=get_start_menu_inline(ROLE_ADMIN))
