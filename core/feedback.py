"""Жалобы и предложения студентов, оценки получения обеда"""
import logging
from datetime import datetime

import aiosqlite
import pytz

from config import BOT_TZ, DB_PATH

logger = logging.getLogger(__name__)

KIND_COMPLAINT = "complaint"
KIND_SUGGESTION = "suggestion"
KIND_LABELS = {KIND_COMPLAINT: "Жалоба", KIND_SUGGESTION: "Предложение"}
KIND_ICONS = {KIND_COMPLAINT: "⚠️", KIND_SUGGESTION: "💡"}


def _now() -> datetime:
    """Местное время бота без смещения (так же хранится issued_at)"""
    return datetime.now(pytz.timezone(BOT_TZ)).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def neutralize(text: str) -> str:
    """Текст студента вставляется в сообщения с HTML-разметкой: MAX вырезает всё, что похоже
    на тег (экранирования в нём нет), поэтому < и > заменяем похожими символами."""
    return (text or "").replace("<", "‹").replace(">", "›")


# ====================
# ЖАЛОБЫ И ПРЕДЛОЖЕНИЯ
# ====================

async def count_feedback_today(user_id: int) -> int:
    today = _now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM feedback WHERE user_id = ? AND DATE(created_at) = ?",
            (user_id, today),
        )
        return (await cur.fetchone())[0]


async def add_feedback(user_id: int, kind: str, text: str) -> int:
    """Сохранить обращение, вернуть его номер"""
    if kind not in KIND_LABELS:
        raise ValueError(f"Недопустимый тип обращения: {kind!r}")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO feedback (user_id, kind, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, kind, text, _fmt(_now())),
        )
        await db.commit()
        return cur.lastrowid


async def count_unread() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM feedback WHERE is_read = 0")
        return (await cur.fetchone())[0]


async def get_recent_feedback(limit: int = 10) -> list:
    """Последние обращения (новые сверху) с ФИО автора; если студент в архиве — берём из архива"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT f.id, f.kind, f.text, f.created_at, f.is_read, f.user_id,
                      COALESCE(u.full_name,
                               (SELECT a.full_name FROM archived_students a WHERE a.user_id = f.user_id LIMIT 1),
                               'неизвестный') AS full_name,
                      COALESCE(u.group_number,
                               (SELECT a.group_number FROM archived_students a WHERE a.user_id = f.user_id LIMIT 1),
                               '—') AS group_number
               FROM feedback f
               LEFT JOIN users u ON u.user_id = f.user_id
               ORDER BY f.id DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def mark_all_read() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("UPDATE feedback SET is_read = 1 WHERE is_read = 0")
        await db.commit()
        return cur.rowcount


async def get_admin_ids() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE role = 'admin'")
        return [int(r[0]) for r in await cur.fetchall()]


# ====================
# ОЦЕНКА ПОЛУЧЕНИЯ ОБЕДА
# ====================

async def save_rating(meal_id: int, user_id: int, rating: int) -> bool:
    """Сохранить оценку (1–5) выдачи. False — если выдача не принадлежит этому студенту.
    Повторное нажатие обновляет оценку, а не создаёт вторую."""
    if not 1 <= int(rating) <= 5:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM meals_log WHERE id = ? AND user_id = ?", (meal_id, user_id)
        )
        if not await cur.fetchone():
            return False
        await db.execute(
            """INSERT INTO meal_ratings (meal_id, user_id, rating, rated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(meal_id) DO UPDATE SET rating = excluded.rating, rated_at = excluded.rated_at""",
            (meal_id, user_id, int(rating), _fmt(_now())),
        )
        await db.commit()
        return True


async def get_rating_stats(from_date: str, to_date: str) -> dict:
    """Средняя оценка и число оценок по выдачам за период"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT AVG(r.rating), COUNT(*) FROM meal_ratings r
               JOIN meals_log m ON m.id = r.meal_id
               WHERE DATE(m.issued_at) BETWEEN ? AND ?""",
            (from_date, to_date),
        )
        avg, count = await cur.fetchone()
        return {"avg": round(avg, 2) if avg is not None else None, "count": count}
