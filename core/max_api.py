"""Отправка личных сообщений пользователям MAX (уведомления)

В MAX id пользователя (user_id) и id его личного диалога с ботом (chat_id) — разные числа.
Вызов bot.send_message(chat_id=<user_id>) уходит в POST /messages?chat_id=<user_id> и
получает 404 error.not.found. Поэтому:

1. если известен chat_id диалога (сохраняется в users.chat_id, когда пользователь
   пишет боту) — шлём через обёртку aiogram-max с этим chat_id;
2. иначе (или если п. 1 не удался) — POST /messages?user_id=<user_id> напрямую:
   MAX сам найдёт личный диалог. Пользователь должен хотя бы раз запустить бота.
"""
import logging

import httpx
from aiogram import Bot

import config
from core.services import get_user_info

logger = logging.getLogger(__name__)


async def _send_by_user_id(user_id: int, text: str) -> None:
    """POST /messages?user_id=... — токен в заголовке Authorization без префикса Bearer"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{config.MAX_API_BASE_URL}/messages",
            params={"user_id": int(user_id)},
            json={"text": text},
            headers={"Authorization": config.MAX_BOT_TOKEN},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"MAX API вернул {resp.status_code}: {resp.text[:200]}")


async def send_text_to_user(bot: Bot, user_id: int, text: str) -> None:
    """Отправить текст пользователю в личку. При неудаче бросает исключение."""
    info = await get_user_info(user_id)
    chat_id = info.get("chat_id") if info else None

    if chat_id:
        try:
            await bot.send_message(chat_id=int(chat_id), text=text)
            return
        except Exception as e:
            logger.warning(
                "Отправка в chat_id=%s не удалась (%s: %s), пробую по user_id=%s",
                chat_id, type(e).__name__, e, user_id,
            )

    await _send_by_user_id(user_id, text)
