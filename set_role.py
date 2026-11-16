"""Назначение роли пользователю по ID в MAX.

Использование:
    python set_role.py 123456789 staff    # права выдачи питания
    python set_role.py 123456789 admin    # права выдачи + админ-команды
    python set_role.py 123456789 student  # обычный получатель

Работает через ту же логику, что и команда /set_role в боте: персонал хранится
в таблице staff_users и не учитывается среди студентов.
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from core.backup import ensure_backup_today
from data.init_db import init_db
from core.services import set_user_role


async def set_role(user_id: int, role: str):
    if config.DB_PATH.exists():
        await ensure_backup_today()  # страховка перед миграцией персонала
    await init_db()  # создаёт staff_users и переносит туда старые записи, если ещё не сделано
    result = await set_user_role(user_id, role)
    if result["status"] == "not_found":
        print(f"❌ Пользователь {user_id} не найден: студент появляется в базе после регистрации в боте.")
    elif result["status"] == "created":
        print(f"✅ Создан пользователь {user_id} с ролью {role}")
    else:
        print(f"✅ Пользователю {user_id} роль изменена: {result['old_role']} -> {role}")
        if result.get("moved_from_students"):
            print("   Запись студента перенесена в таблицу персонала и убрана из списков студентов.")
        if result.get("restored_to_list"):
            print("   Пользователь возвращён в список студентов: ему нужно заново пройти регистрацию (/start).")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in ("student", "staff", "admin"):
        print("Использование: python set_role.py <id> <student|staff|admin>")
        sys.exit(1)
    asyncio.run(set_role(int(sys.argv[1]), sys.argv[2]))
