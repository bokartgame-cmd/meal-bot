"""Бизнес-логика: верификация, генерация кодов, учёт выдач"""
import secrets
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional
from datetime import datetime
import pytz
from config import DB_PATH, BOT_TZ

# Исключаем визуально похожие символы: 0, O, 1, I, l
SAFE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def normalize(text: str) -> str:
    """Нормализация текста: trim, lower, ё->е"""
    if not text:
        return ""
    return text.strip().lower().replace('ё', 'е')


def generate_unique_code(length: int = 6) -> str:
    """Генерация криптостойкого кода"""
    return ''.join(secrets.choice(SAFE_ALPHABET) for _ in range(length))

async def verify_student(user_id: int, full_name: str, group: str, password: str) -> dict:
    """
    Верификация студента: ФИО + группа + личный пароль + актуальность в списке.
    Пароль сверяется с таблицей allowed_students здесь, в ядре.
    Возвращает: {"status": "verified", "code": ...}
    или {"status": "denied", "reason": "not_in_list" | "deactivated" | "already_registered" | "wrong_password"}
    """
    norm_name = normalize(full_name)
    norm_group = normalize(group)
    norm_pass = (password or "").strip().upper()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. Поиск в белом списке
        cur = await db.execute(
            "SELECT password, is_active FROM allowed_students WHERE full_name = ? AND group_number = ?",
            (norm_name, norm_group),
        )
        allowed = await cur.fetchone()
        if not allowed:
            # Студент в архиве (практика, академ) — это «приостановлено», а не «нет в списках»
            from core.archive import is_name_archived
            if await is_name_archived(db, norm_name, norm_group):
                return {"status": "denied", "reason": "deactivated"}
            return {"status": "denied", "reason": "not_in_list"}

        # 2. Студент есть в истории, но НЕ в текущем списке получателей
        if not allowed["is_active"]:
            return {"status": "denied", "reason": "deactivated"}

        # 3. Защита: эти данные уже привязаны к ДРУГОМУ аккаунту
        cur = await db.execute(
            "SELECT user_id FROM users WHERE full_name = ? AND group_number = ? AND user_id != ?",
            (norm_name, norm_group, user_id),
        )
        if await cur.fetchone():
            return {"status": "denied", "reason": "already_registered"}

        # 4. Проверка личного пароля (в ядре, а не в роутере)
        if (allowed["password"] or "").upper() != norm_pass:
            return {"status": "denied", "reason": "wrong_password"}

        # 5. Аккаунт уже верифицирован — возвращаем текущий код
        cur = await db.execute(
            "SELECT unique_code, is_verified, is_active FROM users WHERE user_id = ?",
            (user_id,),
        )
        me = await cur.fetchone()
        if me and me["is_verified"]:
            if not me["is_active"]:
                return {"status": "denied", "reason": "deactivated"}
            return {"status": "verified", "code": me["unique_code"]}

        # 6. Генерация уникального кода (защита от коллизий)
        while True:
            code = generate_unique_code()
            cur = await db.execute("SELECT 1 FROM users WHERE unique_code = ?", (code,))
            if not await cur.fetchone():
                break

        today = datetime.now(pytz.timezone(BOT_TZ)).strftime("%Y-%m-%d")
        if me:
            await db.execute(
                """UPDATE users SET full_name = ?, group_number = ?, unique_code = ?,
                   is_verified = 1, code_date = ? WHERE user_id = ?""",
                (norm_name, norm_group, code, today, user_id),
            )
        else:
            await db.execute(
                """INSERT INTO users (user_id, full_name, group_number, unique_code,
                   is_verified, code_date)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (user_id, norm_name, norm_group, code, today),
            )
        await db.commit()
        return {"status": "verified", "code": code}

async def get_user_info(user_id: int) -> dict | None:
    """Получение информации о пользователе из БД"""
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"GET_INFO: Ищем user_id={user_id} (type={type(user_id)})")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Используем явное приведение типа в запросе для надежности
        cur = await db.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (int(user_id),)
        )
        user = await cur.fetchone()

        if user:
            logger.info(f"GET_INFO SUCCESS: Найдена запись для {user_id}")
            return dict(user)
        else:
            logger.warning(f"GET_INFO FAILED: Запись для {user_id} не найдена")
            return None


async def import_students_from_csv(file_path: str) -> dict:
    """Импорт студентов из CSV-файла в таблицу allowed_students"""
    import pandas as pd

    try:
        df = pd.read_csv(file_path, dtype=str)
        df = df.fillna('')

        # Нормализация данных
        df['full_name'] = df['full_name'].apply(normalize)
        df['group_number'] = df['group_number'].apply(normalize)

        # Убираем пустые строки
        df = df[(df['full_name'] != '') & (df['group_number'] != '')]

        async with aiosqlite.connect(DB_PATH) as db:
            inserted = 0
            for _, row in df.iterrows():
                # Проверяем, есть ли уже такой студент
                cursor = await db.execute(
                    "SELECT 1 FROM allowed_students WHERE full_name = ? AND group_number = ?",
                    (row['full_name'], row['group_number'])
                )
                if not await cursor.fetchone():
                    await db.execute(
                        "INSERT INTO allowed_students (full_name, group_number) VALUES (?, ?)",
                        (row['full_name'], row['group_number'])
                    )
                    inserted += 1

            await db.commit()
            return {"status": "success", "imported": inserted, "total": len(df)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def make_staff(user_id: int) -> bool:
    """Назначить пользователя сотрудником"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE users SET role = 'staff' WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_user_info(user_id: int) -> dict | None:
    """Получить информацию о пользователе"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


VALID_ROLES = ("student", "staff", "admin")

# Имя/группа студента для отчётов: из users, а если студента перенесли в архив — из архива;
# если архив очищен — подпись-заглушка (выдачи при этом остаются в общих итогах).
_NAME_SQL = (
    "COALESCE(u.full_name, (SELECT a.full_name FROM archived_students a "
    "WHERE a.user_id = m.user_id LIMIT 1), 'Удалённый студент')"
)
_GROUP_SQL = (
    "COALESCE(u.group_number, (SELECT a.group_number FROM archived_students a "
    "WHERE a.user_id = m.user_id LIMIT 1), '—')"
)


async def set_user_role(user_id: int, role: str) -> dict:
    """Назначить роль пользователю (для команды /set_role).

    Возвращает {"status": "updated"|"created"|"not_found", "old_role": str | None}.
    - Пользователь есть в БД: меняем роль; для staff/admin заодно подтверждаем
      и активируем его (как в set_role.py). При понижении до student флаги не трогаем,
      чтобы случайно не реактивировать исключённого студента.
    - Пользователя нет: для staff/admin создаём служебную запись (как в set_role.py);
      для student создавать нечего — студент появляется через регистрацию.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"Недопустимая роль: {role!r}")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()

        if row:
            if role == "student":
                await db.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
            else:
                await db.execute(
                    "UPDATE users SET role = ?, is_verified = 1, is_active = 1 WHERE user_id = ?",
                    (role, user_id),
                )
            await db.commit()
            return {"status": "updated", "old_role": row[0]}

        if role == "student":
            return {"status": "not_found", "old_role": None}

        await db.execute(
            """INSERT INTO users (user_id, full_name, group_number, unique_code,
                   is_verified, is_active, role)
               VALUES (?, 'service account', 'service', ?, 1, 1, ?)""",
            (user_id, generate_unique_code(), role),
        )
        await db.commit()
        return {"status": "created", "old_role": None}


async def save_user_chat_id(user_id: int, chat_id: int) -> None:
    """Запомнить chat_id личного диалога пользователя с ботом (нужен для уведомлений)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET chat_id = ? WHERE user_id = ? AND (chat_id IS NULL OR chat_id != ?)",
            (int(chat_id), int(user_id), int(chat_id)),
        )
        await db.commit()


async def get_daily_report(date_str: str = None) -> list:
    """Получить отчет о выдачах за указанную дату (или сегодня)"""
    if not date_str:
        date_str = datetime.now(pytz.timezone(BOT_TZ)).strftime('%Y-%m-%d')

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""SELECT m.issued_at, {_NAME_SQL} AS full_name, {_GROUP_SQL} AS group_number, m.method
               FROM meals_log m
               LEFT JOIN users u ON m.user_id = u.user_id
               WHERE DATE(m.issued_at) = ?
               ORDER BY m.issued_at""",
            (date_str,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

async def get_today_stats() -> dict:
    """Задаток для будущей кнопки «Сегодня»: статистика выдач за текущий день"""
    now = datetime.now(pytz.timezone(BOT_TZ))
    today = now.strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM meals_log WHERE DATE(issued_at) = ?", (today,)
        )
        total = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT method, COUNT(*) FROM meals_log WHERE DATE(issued_at) = ? GROUP BY method",
            (today,),
        )
        by_method = {row[0]: row[1] for row in await cur.fetchall()}
    return {"date": today, "total": total, "by_method": by_method}


async def list_users_with_roles() -> list:
    """Задаток для будущего меню администратора: список пользователей с ролями"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT user_id, full_name, group_number, role, is_active "
            "FROM users ORDER BY registered_at"
        )
        return [dict(row) for row in await cur.fetchall()]

def parse_date(s: str):
    """Парсит дату в формате YYYY-MM-DD, возвращает нормализованную строку или None."""
    s = (s or "").strip()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def period_last_week():
    today = datetime.now(pytz.timezone(BOT_TZ)).date()
    return (today - timedelta(days=6)).isoformat(), today.isoformat()


def period_last_month():
    today = datetime.now(pytz.timezone(BOT_TZ)).date()
    return (today - timedelta(days=29)).isoformat(), today.isoformat()


def period_last_year():
    today = datetime.now(pytz.timezone(BOT_TZ)).date()
    return (today - timedelta(days=364)).isoformat(), today.isoformat()


def _issue_minutes(value) -> Optional[int]:
    """Время получения как число минут от полуночи (по времени бота).
    Новые записи хранятся как местное время без смещения; у старых есть смещение (+03:00) —
    их приводим к часовому поясу бота."""
    s = str(value or "").strip()
    if len(s) < 16:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(pytz.timezone(BOT_TZ))
        return dt.hour * 60 + dt.minute
    except ValueError:
        try:
            return int(s[11:13]) * 60 + int(s[14:16])
        except ValueError:
            return None


def _fmt_minutes(minutes: float) -> str:
    m = min(int(round(minutes)), 24 * 60 - 1)
    return f"{m // 60:02d}:{m % 60:02d}"


def _time_summary(minutes: list) -> Optional[dict]:
    """Минимальное, среднее и максимальное время получения (ЧЧ:ММ)"""
    if not minutes:
        return None
    return {
        "t_min": _fmt_minutes(min(minutes)),
        "t_avg": _fmt_minutes(sum(minutes) / len(minutes)),
        "t_max": _fmt_minutes(max(minutes)),
    }


async def get_stats_for_period(from_date: str, to_date: str) -> dict:
    """
    Статистика выдач за период [from_date, to_date] (включительно).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. Детальная таблица: кто сколько получил
        cur = await db.execute(
            f"""
            SELECT m.user_id AS user_id, {_NAME_SQL} AS full_name,
                   {_GROUP_SQL} AS group_number, COUNT(*) AS cnt
            FROM meals_log m
            LEFT JOIN users u ON u.user_id = m.user_id
            WHERE DATE(m.issued_at) BETWEEN ? AND ?
            GROUP BY m.user_id
            ORDER BY cnt DESC, full_name
            """,
            (from_date, to_date),
        )
        rows = [dict(r) for r in await cur.fetchall()]

        # 1.1. Время получения: min / среднее / max по каждому студенту и по всем выдачам
        cur = await db.execute(
            "SELECT user_id, issued_at FROM meals_log WHERE DATE(issued_at) BETWEEN ? AND ?",
            (from_date, to_date),
        )
        per_user, all_minutes = {}, []
        for r in await cur.fetchall():
            mins = _issue_minutes(r["issued_at"])
            if mins is not None:
                per_user.setdefault(r["user_id"], []).append(mins)
                all_minutes.append(mins)
        for row in rows:
            row.update(_time_summary(per_user.get(row["user_id"])) or
                       {"t_min": "—", "t_avg": "—", "t_max": "—"})
        time_all = _time_summary(all_minutes)

        # 2. Общее количество выданных обедов
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM meals_log WHERE DATE(issued_at) BETWEEN ? AND ?",
            (from_date, to_date),
        )
        total_issued = (await cur.fetchone())["c"]

        # 3. Активные студенты, получившие хотя бы 1 обед (по таблице users, так как они уже зарегистрировались)
        cur = await db.execute(
            """
            SELECT COUNT(DISTINCT m.user_id) AS c
            FROM meals_log m
            JOIN users u ON u.user_id = m.user_id
            WHERE DATE(m.issued_at) BETWEEN ? AND ?
              AND u.is_active = 1 AND u.role = 'student'
            """,
            (from_date, to_date),
        )
        active_got = (await cur.fetchone())["c"]

        # 4. 🔧 ИСПРАВЛЕНО: Всего активных студентов в белом списке (allowed_students)
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM allowed_students WHERE is_active = 1"
        )
        active_total = (await cur.fetchone())["c"]

        return {
            "rows": rows,
            "total_issued": total_issued,
            "active_got": active_got,
            "active_not_got": active_total - active_got,
            "active_total": active_total,
            "time_all": time_all,  # {"t_min", "t_avg", "t_max"} или None, если выдач не было
        }