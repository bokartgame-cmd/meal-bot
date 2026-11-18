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


def _markup_to_attachments(reply_markup) -> list:
    """aiogram InlineKeyboardMarkup -> вложение inline_keyboard формата MAX (для прямого вызова API)"""
    rows = []
    for row in getattr(reply_markup, "inline_keyboard", None) or []:
        buttons = []
        for b in row:
            if getattr(b, "url", None):
                buttons.append({"type": "link", "text": b.text, "url": b.url})
            elif getattr(b, "callback_data", None):
                buttons.append({"type": "callback", "text": b.text, "payload": b.callback_data})
        if buttons:
            rows.append(buttons)
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}] if rows else []


async def _send_by_user_id(user_id: int, text: str, reply_markup=None) -> dict:
    """POST /messages?user_id=... — токен в заголовке Authorization без префикса Bearer.
    Возвращает {"chat_id": ..., "message_id": ...} (значения могут быть None, если ответ нестандартный)"""
    body: dict = {"text": text}
    attachments = _markup_to_attachments(reply_markup) if reply_markup is not None else []
    if attachments:
        body["attachments"] = attachments
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{config.MAX_API_BASE_URL}/messages",
            params={"user_id": int(user_id)},
            json=body,
            headers={"Authorization": config.MAX_BOT_TOKEN},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"MAX API вернул {resp.status_code}: {resp.text[:200]}")
    try:
        msg = (resp.json() or {}).get("message") or {}
        return {
            "chat_id": (msg.get("recipient") or {}).get("chat_id"),
            "message_id": (msg.get("body") or {}).get("mid"),
        }
    except ValueError:
        return {"chat_id": None, "message_id": None}


async def send_text_to_user(bot: Bot, user_id: int, text: str, reply_markup=None) -> dict:
    """Отправить текст (и при необходимости inline-кнопки) пользователю в личку.
    Возвращает {"chat_id", "message_id"} отправленного сообщения (нужны, чтобы потом
    изменить или удалить его). При неудаче бросает исключение."""
    info = await get_user_info(user_id)
    chat_id = info.get("chat_id") if info else None

    if chat_id:
        try:
            sent = await bot.send_message(chat_id=int(chat_id), text=text, reply_markup=reply_markup)
            return {"chat_id": int(chat_id), "message_id": getattr(sent, "message_id", None)}
        except Exception as e:
            logger.warning(
                "Отправка в chat_id=%s не удалась (%s: %s), пробую по user_id=%s",
                chat_id, type(e).__name__, e, user_id,
            )

    return await _send_by_user_id(user_id, text, reply_markup)


async def delete_message(bot: Bot, chat_id, message_id) -> bool:
    """Удалить сообщение бота; ошибки (уже удалено, слишком старое) не бросают исключение"""
    if message_id is None:
        return False
    try:
        if chat_id is not None:
            await bot.delete_message(chat_id=int(chat_id), message_id=message_id)
            return True
    except Exception as e:
        logger.debug("delete_message(chat_id=%s, %s) не удался: %s", chat_id, message_id, e)
    try:  # запасной путь: DELETE /messages?message_id=<mid>
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"{config.MAX_API_BASE_URL}/messages",
                params={"message_id": message_id},
                headers={"Authorization": config.MAX_BOT_TOKEN},
            )
        return resp.status_code == 200
    except Exception as e:
        logger.debug("DELETE /messages %s не удался: %s", message_id, e)
        return False


async def edit_message_text(bot: Bot, chat_id, message_id, text: str) -> bool:
    """Заменить текст сообщения и убрать кнопки. True — если получилось"""
    if chat_id is None or message_id is None:
        return False
    try:
        await bot.edit_message_text(text=text, chat_id=int(chat_id), message_id=message_id,
                                    reply_markup=None)
        return True
    except Exception as e:
        logger.debug("edit_message_text(%s, %s) не удался: %s", chat_id, message_id, e)
        return False
