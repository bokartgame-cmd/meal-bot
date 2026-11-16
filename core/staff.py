"""Сотрудники и администраторы (роли выше student) хранятся отдельно от студентов — в staff_users.

Зачем: студенческие таблицы (users, allowed_students) и всё, что считается по ним
(статистика, ежедневная рассылка QR, импорт списка, архив), не должны задевать персонал.

- Служебный аккаунт (создан командой /set_role по ID): только user_id, роль и chat_id.
- Студент, получивший роль staff/admin, переносится сюда целиком: его запись из users удаляется,
  а строка из allowed_students убирается, чтобы он не попадал в «не получили обед».
  Пароль студента сохраняется здесь, чтобы при понижении до student вернуть его в список.
- История выдач (meals_log) не трогается: имя в отчётах берётся из staff_users.

Функции с аргументом `db` работают внутри чужой транзакции и НЕ делают commit.
"""
from datetime import datetime

import pytz

from config import BOT_TZ

STAFF_ROLES = ("staff", "admin")
SERVICE_GROUP = "service"  # group_number служебных аккаунтов в старой схеме


def _now_str() -> str:
    return datetime.now(pytz.timezone(BOT_TZ)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


async def move_user_to_staff(db, user_id: int, role: str) -> bool:
    """Перенести запись из users в staff_users с ролью role.
    Возвращает True, если запись в users была (иначе ничего не делает)."""
    cur = await db.execute(
        "SELECT full_name, group_number, chat_id, registered_at FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return False
    full_name, group, chat_id, registered_at = row[0], row[1], row[2], row[3]

    password = None
    if group == SERVICE_GROUP:
        full_name = group = None  # служебный аккаунт: личных данных нет
    else:
        cur = await db.execute(
            "SELECT password FROM allowed_students WHERE full_name = ? AND group_number = ?",
            (full_name, group),
        )
        password = next((r[0] for r in await cur.fetchall() if r[0]), None)
        await db.execute(
            "DELETE FROM allowed_students WHERE full_name = ? AND group_number = ?",
            (full_name, group),
        )

    await db.execute(
        "INSERT OR REPLACE INTO staff_users "
        "(user_id, role, full_name, group_number, password, chat_id, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, role, full_name, group, password, chat_id, registered_at or _now_str()),
    )
    await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    return True


async def migrate_staff_out_of_users(db) -> int:
    """Перенести из users всех, у кого роль выше student (запуск при старте, идемпотентно).
    Возвращает число перенесённых записей."""
    cur = await db.execute("SELECT user_id, role FROM users WHERE role IN ('staff', 'admin')")
    rows = [(r[0], r[1]) for r in await cur.fetchall()]
    for user_id, role in rows:
        await move_user_to_staff(db, user_id, role)
    return len(rows)


async def demote_staff(db, user_id: int):
    """Убрать пользователя из staff_users (понижение до student).
    Если он был студентом, возвращаем его в allowed_students с прежним паролем: после /start
    ему нужно заново пройти регистрацию. Возвращает {"old_role", "restored_to_list"} или None."""
    cur = await db.execute(
        "SELECT role, full_name, group_number, password FROM staff_users WHERE user_id = ?",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    old_role, full_name, group, password = row[0], row[1], row[2], row[3]
    await db.execute("DELETE FROM staff_users WHERE user_id = ?", (user_id,))

    restored = False
    if full_name and group:
        cur = await db.execute(
            "SELECT 1 FROM allowed_students WHERE full_name = ? AND group_number = ?",
            (full_name, group),
        )
        if await cur.fetchone() is None:
            if not password:
                from core.services import generate_unique_code  # позже, чтобы не было циклического импорта
                password = generate_unique_code(6)
            await db.execute(
                "INSERT INTO allowed_students (full_name, group_number, password, is_active) "
                "VALUES (?, ?, ?, 1)",
                (full_name, group, password),
            )
        restored = True
    return {"old_role": old_role, "restored_to_list": restored}


async def get_staff_names(db) -> set:
    """Пары (ФИО, группа) персонала, бывшего студентом: импорт списка их пропускает."""
    cur = await db.execute(
        "SELECT full_name, group_number FROM staff_users "
        "WHERE full_name IS NOT NULL AND group_number IS NOT NULL"
    )
    return {(r[0], r[1]) for r in await cur.fetchall()}
