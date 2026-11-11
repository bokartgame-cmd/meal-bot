"""Назначение роли пользователю по Telegram ID.

Использование:
    python set_role.py 123456789 staff    # права выдачи питания
    python set_role.py 123456789 admin    # права выдачи + админ-команды
    python set_role.py 123456789 student  # обычный получатель
"""
import asyncio
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import aiosqlite
from config import DB_PATH

SAFE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_code(length=6):
    return "".join(secrets.choice(SAFE_ALPHABET) for _ in range(length))


async def set_role(user_id: int, role: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE users SET role = ?, is_verified = 1, is_active = 1 WHERE user_id = ?",
                (role, user_id),
            )
            print(f"✅ Пользователю {user_id} роль изменена: {row[0]} -> {role}")
        else:
            await db.execute(
                """INSERT INTO users (user_id, full_name, group_number, unique_code,
                       is_verified, is_active, role)
                   VALUES (?, 'service account', 'service', ?, 1, 1, ?)""",
                (user_id, gen_code(), role),
            )
            print(f"✅ Создан пользователь {user_id} с ролью {role}")
        await db.commit()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in ("student", "staff", "admin"):
        print("Использование: python set_role.py <telegram_id> <student|staff|admin>")
        sys.exit(1)
    asyncio.run(set_role(int(sys.argv[1]), sys.argv[2]))