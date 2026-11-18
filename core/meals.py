"""Новая схема выдачи: права на обед, общий QR, запросы на выдачу, подарки.

Инварианты:
- у студента на каждые сутки (по BOT_TZ) одно право на обед; строка в meal_rights создаётся
  при первом обращении за день (равносильно выдаче права в 00:00) и на другой день не переносится;
- подарок только переносит право от одного студента другому, сумма прав за сутки не растёт;
- право резервируется, когда студент подтвердил запрос; в журнал (meals_log) выдача попадает
  только после подтверждения сотрудником; без подтверждения право возвращается по таймауту;
- каждое изменение прав — одна атомарная операция в БД (условный UPDATE / BEGIN IMMEDIATE),
  поэтому две одновременные попытки не могут обойти проверку.
"""
import logging
import secrets
from datetime import datetime, timedelta

import aiosqlite
import pytz

import config
from config import BOT_TZ, DB_PATH

logger = logging.getLogger(__name__)

METHOD_STAFF_CONFIRM = "qr_staff"   # выдача по общему QR с подтверждением сотрудника
GIFT_PREFIX = "gift_"               # префикс start-параметра диплинка подарка (лимит start — 128 символов)


# ====================
# ВРЕМЯ
# ====================

def now_local() -> datetime:
    """Местное время бота без смещения (так же хранится issued_at)"""
    return datetime.now(pytz.timezone(BOT_TZ)).replace(tzinfo=None)


def today_str() -> str:
    return now_local().strftime("%Y-%m-%d")


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ====================
# ВСПОМОГАТЕЛЬНОЕ
# ====================

def short_name(full_name: str, group: str) -> str:
    """«иванов иван иванович», «28-143» -> «Иванов И., 28-143»"""
    parts = (full_name or "").split()
    if not parts:
        name = "Студент"
    elif len(parts) == 1:
        name = parts[0].capitalize()
    else:
        name = f"{parts[0].capitalize()} {parts[1][0].upper()}."
    return f"{name}, {(group or '').upper()}"


def _connect():
    return aiosqlite.connect(DB_PATH, isolation_level=None)  # транзакции ведём вручную


async def _begin(db):
    await db.execute("BEGIN IMMEDIATE")


async def _get_student(db, user_id: int):
    """Студент, который вправе получать обеды: роль student, прошёл регистрацию и активен"""
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT user_id, full_name, group_number FROM users "
        "WHERE user_id = ? AND role = 'student' AND is_verified = 1 AND is_active = 1",
        (int(user_id),),
    )
    return await cur.fetchone()


async def _ensure_rights(db, user_id: int, day: str) -> int:
    """Создать строку прав на день (одно право), если её ещё нет; вернуть текущее число прав"""
    await db.execute(
        "INSERT OR IGNORE INTO meal_rights (user_id, day, rights) VALUES (?, ?, 1)",
        (int(user_id), day),
    )
    cur = await db.execute(
        "SELECT rights FROM meal_rights WHERE user_id = ? AND day = ?", (int(user_id), day)
    )
    return (await cur.fetchone())[0]


# ====================
# ПРАВА
# ====================

async def get_rights(user_id: int) -> int | None:
    """Сколько прав на обед у студента сегодня; None — если он не может получать обеды"""
    day = today_str()
    async with _connect() as db:
        await _begin(db)
        try:
            if not await _get_student(db, user_id):
                await db.execute("ROLLBACK")
                return None
            rights = await _ensure_rights(db, user_id, day)
            await db.execute("COMMIT")
            return rights
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def consume_one_right(db, user_id: int, day: str) -> bool:
    """Списать одно право (для запасного режима со сканированием личного QR).
    Вызывается внутри открытой транзакции."""
    await _ensure_rights(db, user_id, day)
    cur = await db.execute(
        "UPDATE meal_rights SET rights = rights - 1 WHERE user_id = ? AND day = ? AND rights > 0",
        (int(user_id), day),
    )
    return cur.rowcount == 1


# ====================
# СМЕНА ВЫДАЧИ
# ====================

async def start_issuance(staff_id: int) -> dict:
    """Сотрудник нажал «Начать выдачу»: с этого момента и до конца суток выдаёт он.
    Возвращает {"previous_staff_id": int | None} — прежний выдающий (если был другой)."""
    day = today_str()
    async with _connect() as db:
        await _begin(db)
        try:
            cur = await db.execute("SELECT staff_id FROM issuance_sessions WHERE day = ?", (day,))
            row = await cur.fetchone()
            previous = row[0] if row and int(row[0]) != int(staff_id) else None
            await db.execute(
                """INSERT INTO issuance_sessions (day, staff_id, started_at) VALUES (?, ?, ?)
                   ON CONFLICT(day) DO UPDATE SET staff_id = excluded.staff_id,
                                                  started_at = excluded.started_at""",
                (day, int(staff_id), _fmt(now_local())),
            )
            await db.execute("COMMIT")
            return {"previous_staff_id": previous}
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def get_active_staff() -> int | None:
    """Кто выдаёт сегодня (None — выдача не начата)"""
    async with _connect() as db:
        cur = await db.execute(
            "SELECT staff_id FROM issuance_sessions WHERE day = ?", (today_str(),)
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None


# ====================
# ЗАПРОС НА ВЫДАЧУ
# ====================

async def check_scan(user_id: int) -> dict:
    """Проверка при скане общего QR (ничего не списывает).
    status: ok (+rights) | not_student | no_session | no_rights | pending (запрос уже отправлен)"""
    day = today_str()
    async with _connect() as db:
        await _begin(db)
        try:
            if not await _get_student(db, user_id):
                await db.execute("ROLLBACK")
                return {"status": "not_student"}
            cur = await db.execute("SELECT 1 FROM issuance_sessions WHERE day = ?", (day,))
            if not await cur.fetchone():
                await db.execute("ROLLBACK")
                return {"status": "no_session"}
            cur = await db.execute(
                "SELECT 1 FROM meal_requests WHERE user_id = ? AND day = ? AND status = 'pending'",
                (int(user_id), day),
            )
            if await cur.fetchone():
                await db.execute("ROLLBACK")
                return {"status": "pending"}
            rights = await _ensure_rights(db, user_id, day)
            await db.execute("COMMIT")
            return {"status": "ok" if rights > 0 else "no_rights", "rights": rights}
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def reserve_request(user_id: int) -> dict:
    """Студент нажал «Подтвердить»: все его права на сегодня резервируются под запрос.
    status: ok | not_student | no_session | no_rights | pending
    При ok: request_id, count, staff_id, name (для сообщения сотруднику)."""
    day = today_str()
    now = now_local()
    async with _connect() as db:
        await _begin(db)
        try:
            student = await _get_student(db, user_id)
            if not student:
                await db.execute("ROLLBACK")
                return {"status": "not_student"}
            cur = await db.execute("SELECT staff_id FROM issuance_sessions WHERE day = ?", (day,))
            session = await cur.fetchone()
            if not session:
                await db.execute("ROLLBACK")
                return {"status": "no_session"}
            cur = await db.execute(
                "SELECT 1 FROM meal_requests WHERE user_id = ? AND day = ? AND status = 'pending'",
                (int(user_id), day),
            )
            if await cur.fetchone():
                await db.execute("ROLLBACK")
                return {"status": "pending"}
            rights = await _ensure_rights(db, user_id, day)
            if rights <= 0:
                await db.execute("ROLLBACK")
                return {"status": "no_rights"}
            await db.execute(
                "UPDATE meal_rights SET rights = rights - ? WHERE user_id = ? AND day = ? AND rights >= ?",
                (rights, int(user_id), day, rights),
            )
            cur = await db.execute(
                """INSERT INTO meal_requests (user_id, day, count, status, staff_id, created_at, expires_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (int(user_id), day, rights, int(session[0]), _fmt(now),
                 _fmt(now + timedelta(minutes=config.MEAL_CONFIRM_TIMEOUT_MIN))),
            )
            request_id = cur.lastrowid
            await db.execute("COMMIT")
            return {
                "status": "ok",
                "request_id": request_id,
                "count": rights,
                "staff_id": int(session[0]),
                "name": short_name(student["full_name"], student["group_number"]),
            }
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def _release(db, request_id: int, new_status: str) -> dict | None:
    """Перевести pending-запрос в new_status и вернуть права (если сутки ещё не закончились).
    Внутри открытой транзакции. None — запрос уже не pending."""
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "UPDATE meal_requests SET status = ? WHERE id = ? AND status = 'pending'",
        (new_status, request_id),
    )
    if cur.rowcount != 1:
        return None
    cur = await db.execute("SELECT * FROM meal_requests WHERE id = ?", (request_id,))
    req = dict(await cur.fetchone())
    restored = req["day"] == today_str()  # права прошлых суток сгорели
    if restored:
        await db.execute(
            "UPDATE meal_rights SET rights = rights + ? WHERE user_id = ? AND day = ?",
            (req["count"], req["user_id"], req["day"]),
        )
    req["restored"] = restored
    return req


async def cancel_request(request_id: int) -> bool:
    """Отмена запроса (например, сообщение сотруднику не доставлено): права возвращаются"""
    async with _connect() as db:
        await _begin(db)
        try:
            req = await _release(db, request_id, "cancelled")
            await db.execute("COMMIT")
            return req is not None
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def attach_staff_message(request_id: int, chat_id, message_id) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE meal_requests SET staff_chat_id = ?, staff_message_id = ?, staff_msg_deleted = 0 "
            "WHERE id = ?",
            (int(chat_id) if chat_id is not None else None,
             str(message_id) if message_id is not None else None, request_id),
        )


async def get_request(request_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM meal_requests WHERE id = ?", (request_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def confirm_issuance(request_id: int, staff_id: int) -> dict:
    """Сотрудник нажал «Подтвердить»: только теперь выдача пишется в журнал.
    status: ok (+user_id, count, meal_ids) | not_found | forbidden | done (уже подтверждено) |
            expired (время вышло / запрос отменён)"""
    now = now_local()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        await _begin(db)
        try:
            cur = await db.execute("SELECT * FROM meal_requests WHERE id = ?", (request_id,))
            row = await cur.fetchone()
            if not row:
                await db.execute("ROLLBACK")
                return {"status": "not_found"}
            req = dict(row)
            # подтверждать может только тот, кто сегодня выдаёт (запрос переадресуется при передаче выдачи)
            cur = await db.execute("SELECT staff_id FROM issuance_sessions WHERE day = ?", (today_str(),))
            session = await cur.fetchone()
            if int(req["staff_id"]) != int(staff_id) or not session or int(session[0]) != int(staff_id):
                await db.execute("ROLLBACK")
                return {"status": "forbidden"}
            if req["status"] == "confirmed":
                await db.execute("ROLLBACK")
                return {"status": "done"}
            cur = await db.execute(
                "UPDATE meal_requests SET status = 'confirmed', confirmed_at = ? "
                "WHERE id = ? AND status = 'pending' AND expires_at > ? AND day = ?",
                (_fmt(now), request_id, _fmt(now), today_str()),
            )
            if cur.rowcount != 1:
                await db.execute("ROLLBACK")
                return {"status": "expired"}
            meal_ids = []
            for _ in range(req["count"]):
                cur = await db.execute(
                    "INSERT INTO meals_log (user_id, issued_at, issued_by, method, request_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (req["user_id"], _fmt(now), int(staff_id), METHOD_STAFF_CONFIRM, request_id),
                )
                meal_ids.append(cur.lastrowid)
            await db.execute("COMMIT")
            return {"status": "ok", "user_id": req["user_id"], "count": req["count"],
                    "meal_ids": meal_ids, "request": req}
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def expire_pending() -> list:
    """Запросы без подтверждения сотрудника, у которых вышло время: права возвращаются студенту.
    Возвращает список просроченных запросов (с ключом restored)."""
    now = _fmt(now_local())
    expired = []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        await _begin(db)
        try:
            cur = await db.execute(
                "SELECT id FROM meal_requests WHERE status = 'pending' AND expires_at <= ?", (now,)
            )
            ids = [r[0] for r in await cur.fetchall()]
            for rid in ids:
                req = await _release(db, rid, "expired")
                if req:
                    expired.append(req)
            await db.execute("COMMIT")
        except Exception:
            await db.execute("ROLLBACK")
            raise
    return expired


async def reassign_pending(new_staff_id: int) -> list:
    """При передаче выдачи другому сотруднику: неподтверждённые запросы сегодняшнего дня
    переходят к нему. Возвращает эти запросы (с именем студента) для повторной отправки."""
    day = today_str()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        await _begin(db)
        try:
            await db.execute(
                "UPDATE meal_requests SET staff_id = ? WHERE day = ? AND status = 'pending'",
                (int(new_staff_id), day),
            )
            cur = await db.execute(
                """SELECT r.*, u.full_name, u.group_number FROM meal_requests r
                   JOIN users u ON u.user_id = r.user_id
                   WHERE r.day = ? AND r.status = 'pending' ORDER BY r.id""",
                (day,),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            await db.execute("COMMIT")
        except Exception:
            await db.execute("ROLLBACK")
            raise
    for r in rows:
        r["name"] = short_name(r["full_name"], r["group_number"])
    return rows


async def pop_confirmed_messages(staff_id: int, keep_request_id: int | None = None) -> list:
    """Подтверждённые сообщения сотрудника, которые пора удалить из чата (когда пришло новое).
    Возвращает [{"id", "chat_id", "message_id"}] и помечает их удалёнными."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        await _begin(db)
        try:
            cur = await db.execute(
                """SELECT id, staff_chat_id AS chat_id, staff_message_id AS message_id
                   FROM meal_requests
                   WHERE staff_id = ? AND day = ? AND status = 'confirmed' AND staff_msg_deleted = 0
                     AND staff_message_id IS NOT NULL AND id != ?""",
                (int(staff_id), today_str(), keep_request_id or 0),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            for r in rows:
                await db.execute("UPDATE meal_requests SET staff_msg_deleted = 1 WHERE id = ?", (r["id"],))
            await db.execute("COMMIT")
            return rows
        except Exception:
            await db.execute("ROLLBACK")
            raise


# ====================
# ПОДАРКИ
# ====================

async def create_gift(user_id: int) -> dict:
    """«Угостить друга»: право списывается у дарителя сразу, создаётся одноразовый токен.
    status: ok (+token) | not_student | no_rights"""
    day = today_str()
    token = secrets.token_hex(8)
    async with _connect() as db:
        await _begin(db)
        try:
            if not await _get_student(db, user_id):
                await db.execute("ROLLBACK")
                return {"status": "not_student"}
            await _ensure_rights(db, user_id, day)
            cur = await db.execute(
                "UPDATE meal_rights SET rights = rights - 1 WHERE user_id = ? AND day = ? AND rights > 0",
                (int(user_id), day),
            )
            if cur.rowcount != 1:
                await db.execute("ROLLBACK")
                return {"status": "no_rights"}
            await db.execute(
                "INSERT INTO meal_gifts (token, giver_id, day, status, created_at) VALUES (?, ?, ?, 'active', ?)",
                (token, int(user_id), day, _fmt(now_local())),
            )
            await db.execute("COMMIT")
            return {"status": "ok", "token": token}
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def redeem_gift(token: str, user_id: int) -> dict:
    """Друг открыл ссылку: право переносится ему. Ссылка срабатывает один раз
    (условный UPDATE статуса и перенос права — в одной транзакции).
    status: ok (+rights, giver_id) | not_student | invalid | used | revoked | expired | own"""
    day = today_str()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        await _begin(db)
        try:
            if not await _get_student(db, user_id):
                await db.execute("ROLLBACK")
                return {"status": "not_student"}
            cur = await db.execute("SELECT * FROM meal_gifts WHERE token = ?", (token,))
            gift = await cur.fetchone()
            if not gift:
                await db.execute("ROLLBACK")
                return {"status": "invalid"}
            if int(gift["giver_id"]) == int(user_id):
                await db.execute("ROLLBACK")
                return {"status": "own"}
            if gift["status"] != "active":
                await db.execute("ROLLBACK")
                return {"status": gift["status"]}   # used | revoked
            if gift["day"] != day:
                await db.execute("ROLLBACK")
                return {"status": "expired"}        # право прошлых суток сгорело
            cur = await db.execute(
                "UPDATE meal_gifts SET status = 'used', receiver_id = ?, used_at = ? "
                "WHERE token = ? AND status = 'active'",
                (int(user_id), _fmt(now_local()), token),
            )
            if cur.rowcount != 1:
                await db.execute("ROLLBACK")
                return {"status": "used"}
            await _ensure_rights(db, user_id, day)  # своё право создаём ДО подарка
            await db.execute(
                "UPDATE meal_rights SET rights = rights + 1 WHERE user_id = ? AND day = ?",
                (int(user_id), day),
            )
            cur = await db.execute(
                "SELECT rights FROM meal_rights WHERE user_id = ? AND day = ?", (int(user_id), day)
            )
            rights = (await cur.fetchone())[0]
            await db.execute("COMMIT")
            return {"status": "ok", "rights": rights, "giver_id": int(gift["giver_id"])}
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def revoke_gift(token: str, user_id: int) -> dict:
    """Даритель забирает право обратно, пока друг не открыл ссылку (до конца суток).
    status: ok | not_active (ссылка уже использована/отозвана) | not_found | expired"""
    day = today_str()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        await _begin(db)
        try:
            cur = await db.execute(
                "SELECT * FROM meal_gifts WHERE token = ? AND giver_id = ?", (token, int(user_id))
            )
            gift = await cur.fetchone()
            if not gift:
                await db.execute("ROLLBACK")
                return {"status": "not_found"}
            if gift["day"] != day:
                await db.execute("ROLLBACK")
                return {"status": "expired"}
            cur = await db.execute(
                "UPDATE meal_gifts SET status = 'revoked' WHERE token = ? AND status = 'active'", (token,)
            )
            if cur.rowcount != 1:
                await db.execute("ROLLBACK")
                return {"status": "not_active"}
            await db.execute(
                "UPDATE meal_rights SET rights = rights + 1 WHERE user_id = ? AND day = ?",
                (int(user_id), day),
            )
            await db.execute("COMMIT")
            return {"status": "ok"}
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def list_active_gifts(user_id: int) -> list:
    """Неиспользованные подарочные ссылки студента на сегодня (токены)"""
    async with _connect() as db:
        cur = await db.execute(
            "SELECT token FROM meal_gifts WHERE giver_id = ? AND day = ? AND status = 'active' ORDER BY created_at",
            (int(user_id), today_str()),
        )
        return [r[0] for r in await cur.fetchall()]


async def get_gifts_for_period(from_date: str, to_date: str) -> list:
    """Журнал подарков за период (для Excel): кто кого угостил"""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT g.day, g.created_at, g.used_at, g.status,
                      COALESCE(gu.full_name, '—') AS giver_name, COALESCE(gu.group_number, '—') AS giver_group,
                      COALESCE(ru.full_name, '—') AS receiver_name, COALESCE(ru.group_number, '—') AS receiver_group
               FROM meal_gifts g
               LEFT JOIN users gu ON gu.user_id = g.giver_id
               LEFT JOIN users ru ON ru.user_id = g.receiver_id
               WHERE g.day BETWEEN ? AND ?
               ORDER BY g.created_at""",
            (from_date, to_date),
        )
        return [dict(r) for r in await cur.fetchall()]
