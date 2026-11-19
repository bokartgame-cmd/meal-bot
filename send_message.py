"""Отправить сообщение пользователю MAX с конкретным user_id (от имени бота).

Примеры:
    python send_message.py 622026003 "Привет! Тестовое сообщение"
    python send_message.py 622026003 --file text.txt
    echo "Текст" | python send_message.py 622026003
    python send_message.py 622026003 "Текст" --yes          # без подтверждения
    python send_message.py 622026003 "<b>Жирный</b>" --format html

Токен берётся из .env (MAX_BOT_TOKEN). Сообщение уходит запросом POST /messages?user_id=...:
MAX сам находит личный диалог, но пользователь должен хотя бы раз запустить бота.
Без --format текст отправляется как обычный (ссылки с «_» и спецсимволы не искажаются).
"""
import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

import httpx

import config

MAX_TEXT_LEN = 4000  # лимит длины текста сообщения в MAX


def lookup_user(user_id: int):
    """(ФИО, группа, роль) из БД бота или None, если пользователя там нет"""
    if not Path(config.DB_PATH).exists():
        return None
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return conn.execute(
            "SELECT full_name, group_number, role FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()


async def send(user_id: int, text: str, fmt: str | None) -> dict:
    body = {"text": text}
    if fmt:
        body["format"] = fmt
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{config.MAX_API_BASE_URL}/messages",
            params={"user_id": user_id},
            json=body,
            headers={"Authorization": config.MAX_BOT_TOKEN},  # токен без префикса Bearer
        )
    if resp.status_code != 200:
        hint = ""
        if resp.status_code in (400, 404):
            hint = " (пользователь не найден или ещё не запускал бота)"
        elif resp.status_code == 401:
            hint = " (проверьте MAX_BOT_TOKEN в .env)"
        raise RuntimeError(f"MAX API вернул {resp.status_code}{hint}: {resp.text[:300]}")
    return resp.json() or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Отправить сообщение пользователю MAX по user_id")
    parser.add_argument("user_id", type=int, help="id пользователя MAX")
    parser.add_argument("text", nargs="?", help="текст сообщения (или --file, или stdin)")
    parser.add_argument("--file", "-f", type=Path, help="взять текст из файла (UTF-8)")
    parser.add_argument("--format", choices=("html", "markdown"), help="разметка текста (по умолчанию — обычный текст)")
    parser.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждение")
    args = parser.parse_args()

    if args.file:
        text = args.file.read_text(encoding="utf-8")
    elif args.text is not None:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        parser.error("укажите текст, --file или передайте его через stdin")
    text = text.strip()

    if not text:
        print("❌ Пустое сообщение.")
        return 1
    if len(text) > MAX_TEXT_LEN:
        print(f"❌ Текст длиннее {MAX_TEXT_LEN} символов ({len(text)}).")
        return 1
    if not config.MAX_BOT_TOKEN:
        print("❌ MAX_BOT_TOKEN не задан (файл .env).")
        return 1

    user = lookup_user(args.user_id)
    who = f"{user[0].title()} ({user[1].upper()}, {user[2]})" if user else "нет в БД бота"
    print(f"Получатель: {args.user_id} — {who}")
    print(f"Текст ({len(text)} симв.):\n{text}\n")
    if not args.yes and input("Отправить? [y/N] ").strip().lower() not in ("y", "yes", "д", "да"):
        print("Отменено.")
        return 0

    try:
        result = asyncio.run(send(args.user_id, text, args.format))
    except (RuntimeError, httpx.HTTPError) as e:
        print(f"❌ Не отправлено: {e}")
        return 1
    mid = ((result.get("message") or {}).get("body") or {}).get("mid")
    print(f"✅ Отправлено{f' (id сообщения: {mid})' if mid else ''}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
