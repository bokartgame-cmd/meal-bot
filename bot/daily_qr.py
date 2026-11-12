"""Ежедневная автоматическая рассылка QR-карточек студентам.

Фоновая задача раз в минуту проверяет: наступило ли время рассылки (config.DAILY_QR_HOUR:MINUTE
по BOT_TZ) и есть ли студенты, которым сегодня QR ещё не отправлен (users.qr_sent_date).
Это даёт:
- идемпотентность: повторные проверки и перезапуск бота не приводят к дублям;
- «догон»: если бот был выключен в момент рассылки, QR уйдут сразу после запуска (в тот же день).

Рассылка идёт только тем, чей chat_id личного диалога известен (он сохраняется, когда
студент пишет боту). Остальные пропускаются и получат QR, как только обратятся к боту.
"""
import asyncio
import logging
from datetime import datetime

import pytz
from aiogram import Bot
from aiogram.types import BufferedInputFile

import config
from bot.keyboards import get_qr_menu_inline
from bot.routers.user import student_messages
from core.qr import generate_diplink, generate_qr_card_bytes
from core.services import (
    count_students_without_chat,
    get_active_code,
    get_students_for_daily_qr,
    mark_qr_sent,
)

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60
SEND_PAUSE_SECONDS = 0.1  # небольшая пауза между отправками, чтобы не упереться в лимиты API


def _today() -> str:
    return datetime.now(pytz.timezone(config.BOT_TZ)).strftime("%Y-%m-%d")


def _is_send_time() -> bool:
    now = datetime.now(pytz.timezone(config.BOT_TZ))
    return (now.hour, now.minute) >= (config.DAILY_QR_HOUR, config.DAILY_QR_MINUTE)


async def _send_card(bot: Bot, user_id: int, chat_id: int) -> bool:
    """Отправить студенту свежую QR-карточку. True — если отправлено."""
    code = await get_active_code(user_id)  # новый код на сегодня; "" для неактивных
    if not code:
        return False

    # Удаляем вчерашнюю карточку, если помним её id (после перезапуска бота — не помним)
    old_id = student_messages.get(user_id, {}).get("qr_message_id")
    if old_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception as e:
            logger.debug("Не удалось удалить старую QR-карточку у %s: %s", user_id, e)

    qr_buf = generate_qr_card_bytes(generate_diplink(code, is_max=True), code)
    sent = await bot.send_photo(
        chat_id=chat_id,
        photo=BufferedInputFile(qr_buf.read(), filename=f"qr_{code}.png"),
        caption=(
            f"🌅 Доброе утро! Ваш QR-код на сегодня: {code}\n"
            f"Покажите его сотруднику столовой.\n"
            f"⚠️ Код обновляется каждый день, вчерашний QR не сработает."
        ),
        reply_markup=get_qr_menu_inline(),
    )
    student_messages.setdefault(user_id, {})["qr_message_id"] = sent.message_id
    return True


async def send_daily_qr(bot: Bot, failed_today: set) -> tuple:
    """Разослать QR всем, кому ещё не отправлено сегодня. Возвращает (отправлено, ошибок)."""
    today = _today()
    ok = failed = 0
    for st in await get_students_for_daily_qr(today):
        uid = st["user_id"]
        if uid in failed_today:
            continue  # сегодня уже не получилось — не долбим каждую минуту
        try:
            if await _send_card(bot, uid, int(st["chat_id"])):
                await mark_qr_sent(uid, today)
                ok += 1
        except Exception as e:
            failed += 1
            failed_today.add(uid)
            logger.warning("Не удалось отправить ежедневный QR студенту %s: %s: %s",
                           uid, type(e).__name__, e)
        await asyncio.sleep(SEND_PAUSE_SECONDS)
    return ok, failed


async def daily_qr_loop(bot: Bot) -> None:
    """Бесконечный цикл рассылки. Запускается из bot/main.py фоновой задачей."""
    logger.info("📅 Ежедневная рассылка QR включена: %02d:%02d (%s)",
                config.DAILY_QR_HOUR, config.DAILY_QR_MINUTE, config.BOT_TZ)
    failed_today: set = set()
    failed_date = _today()
    while True:
        try:
            today = _today()
            if today != failed_date:  # наступил новый день — даём второй шанс неудачникам
                failed_today.clear()
                failed_date = today
            if _is_send_time():
                ok, failed = await send_daily_qr(bot, failed_today)
                if ok or failed:
                    skipped = await count_students_without_chat(today)
                    logger.info("📬 Ежедневный QR: отправлено %d, ошибок %d, пропущено без chat_id %d",
                                ok, failed, skipped)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в цикле ежедневной рассылки QR")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
