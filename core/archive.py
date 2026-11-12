"""Архив неактивных студентов.

Студент становится неактивным, когда его нет в загруженном списке: практика, академ,
отчисление. Чтобы основные таблицы не разрастались, такие студенты переносятся
в archived_students. Из архива студент возвращается автоматически, как только снова
появляется в списке при импорте: с прежним паролем, аккаунтом (user_id, chat_id)
и без повторной регистрации.

История выдач (meals_log) при переносе не трогается: имена для статистики берутся
из архива, а после очистки архива выдачи остаются только в общих итогах.

Функции с аргументом `db` работают внутри чужой транзакции и НЕ делают commit.
"""
import logging
from datetime import datetime, timedelta

import aiosqlite
import pytz

from config import BOT_TZ, DB_PATH
from core.services import generate_unique_code

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """Местное время бота без смещения (так же хранится issued_at)"""
    return datetime.now(pytz.timezone(BOT_TZ)).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def archive_student(db, full_name: str, group: str) -> bool:
    """Перенести студента (ФИО + группа) из allowed_students и users в архив.
    Возвращает True, если было что переносить."""
    cur = await db.execute(
        "SELECT password FROM allowed_students WHERE full_name = ? AND group_number = ?",
        (full_name, group),
    )
    allowed = await cur.fetchall()
    cur = await db.execute(
        "SELECT user_id, chat_id, registered_at FROM users "
        "WHERE full_name = ? AND group_number = ? AND role = 'student'",
        (full_name, group),
    )
    users = await cur.fetchall()
    if not allowed and not users:
        return False

    password = next((r[0] for r in allowed if r[0]), None)
    now = _fmt(_now())
    insert = (
        "INSERT INTO archived_students "
        "(full_name, group_number, password, user_id, chat_id, registered_at, archived_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    if users:
        for u in users:
            await db.execute(insert, (full_name, group, password, u[0], u[1], u[2], now))
    else:
        await db.execute(insert, (full_name, group, password, None, None, None, now))

    await db.execute(
        "DELETE FROM users WHERE full_name = ? AND group_number = ? AND role = 'student'",
        (full_name, group),
    )
    await db.execute(
        "DELETE FROM allowed_students WHERE full_name = ? AND group_number = ?",
        (full_name, group),
    )
    return True


async def archive_inactive(db) -> int:
    """Перенести в архив всех студентов с is_active = 0 (остатки старой схемы).
    Возвращает число перенесённых студентов."""
    cur = await db.execute(
        """SELECT full_name, group_number FROM allowed_students WHERE is_active = 0
           UNION
           SELECT u.full_name, u.group_number FROM users u
           WHERE u.role = 'student' AND u.is_active = 0
             AND NOT EXISTS (SELECT 1 FROM allowed_students a
                             WHERE a.full_name = u.full_name
                               AND a.group_number = u.group_number
                               AND a.is_active = 1)"""
    )
    keys = [(r[0], r[1]) for r in await cur.fetchall()]
    moved = 0
    for name, group in keys:
        if await archive_student(db, name, group):
            moved += 1
    return moved


async def archive_inactive_students() -> int:
    """Отдельная транзакция для запуска при старте бота (идемпотентно)."""
    async with aiosqlite.connect(DB_PATH) as db:
        moved = await archive_inactive(db)
        await db.commit()
    if moved:
        logger.info("🗄 В архив перенесено неактивных студентов: %d", moved)
    return moved


async def get_archived(db, full_name: str, group: str):
    """Данные архивной записи или None, если студента в архиве нет."""
    cur = await db.execute(
        "SELECT password FROM archived_students WHERE full_name = ? AND group_number = ? "
        "ORDER BY id LIMIT 1",
        (full_name, group),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    cur = await db.execute(
        "SELECT password FROM archived_students WHERE full_name = ? AND group_number = ? "
        "AND password IS NOT NULL AND password != '' LIMIT 1",
        (full_name, group),
    )
    pwd_row = await cur.fetchone()
    return {"password": pwd_row[0] if pwd_row else None}


async def restore_from_archive(db, full_name: str, group: str, password: str) -> int:
    """Вернуть студента из архива в allowed_students и users (с прежним user_id и chat_id).
    Возвращает число восстановленных аккаунтов."""
    cur = await db.execute(
        "SELECT user_id, chat_id, registered_at FROM archived_students "
        "WHERE full_name = ? AND group_number = ?",
        (full_name, group),
    )
    rows = await cur.fetchall()
    if not rows:
        return 0

    cur = await db.execute(
        "SELECT id FROM allowed_students WHERE full_name = ? AND group_number = ?",
        (full_name, group),
    )
    if await cur.fetchone():
        await db.execute(
            "UPDATE allowed_students SET is_active = 1, password = ? "
            "WHERE full_name = ? AND group_number = ?",
            (password, full_name, group),
        )
    else:
        await db.execute(
            "INSERT INTO allowed_students (full_name, group_number, password, is_active) "
            "VALUES (?, ?, ?, 1)",
            (full_name, group, password),
        )

    restored = 0
    for r in rows:
        user_id = r[0]
        if user_id is None:
            continue
        cur = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        if await cur.fetchone():
            continue  # такой аккаунт уже есть — не затираем
        while True:
            code = generate_unique_code()
            cur = await db.execute("SELECT 1 FROM users WHERE unique_code = ?", (code,))
            if not await cur.fetchone():
                break
        await db.execute(
            "INSERT INTO users (user_id, full_name, group_number, unique_code, is_verified, "
            "is_active, role, registered_at, chat_id) "
            "VALUES (?, ?, ?, ?, 1, 1, 'student', COALESCE(?, CURRENT_TIMESTAMP), ?)",
            (user_id, full_name, group, code, r[2], r[1]),
        )
        restored += 1

    await db.execute(
        "DELETE FROM archived_students WHERE full_name = ? AND group_number = ?",
        (full_name, group),
    )
    return restored


async def get_archived_passwords(db) -> set:
    """Пароли архивных студентов (чтобы новые пароли не совпадали с ними)"""
    cur = await db.execute(
        "SELECT password FROM archived_students WHERE password IS NOT NULL AND password != ''"
    )
    return {r[0] for r in await cur.fetchall()}


async def is_user_archived(user_id: int) -> bool:
    """Есть ли аккаунт среди архивных студентов (для сообщения при /start)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM archived_students WHERE user_id = ? LIMIT 1", (user_id,)
        )
        return await cur.fetchone() is not None


async def is_name_archived(db, full_name: str, group: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM archived_students WHERE full_name = ? AND group_number = ? LIMIT 1",
        (full_name, group),
    )
    return await cur.fetchone() is not None


def _old_cutoff(older_than_days: int) -> str:
    return _fmt(_now() - timedelta(days=older_than_days))


async def count_archived(older_than_days: int | None = None) -> int:
    """Число студентов в архиве (всего или только пролежавших дольше N дней)"""
    sql = ("SELECT COUNT(*) FROM (SELECT DISTINCT full_name, group_number "
           "FROM archived_students{where})")
    params: tuple = ()
    where = ""
    if older_than_days is not None:
        where = " WHERE archived_at < ?"
        params = (_old_cutoff(older_than_days),)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql.format(where=where), params)
        return (await cur.fetchone())[0]


async def clear_archive(older_than_days: int | None = None) -> int:
    """Безвозвратно удалить архив (весь или только записи старше N дней).
    Возвращает число удалённых студентов."""
    deleted = await count_archived(older_than_days)
    async with aiosqlite.connect(DB_PATH) as db:
        if older_than_days is None:
            await db.execute("DELETE FROM archived_students")
        else:
            await db.execute(
                "DELETE FROM archived_students WHERE archived_at < ?",
                (_old_cutoff(older_than_days),),
            )
        await db.commit()
    logger.warning("🗑 Очищен архив студентов: удалено %d (старше %s дн.)",
                   deleted, older_than_days if older_than_days is not None else "—")
    return deleted
