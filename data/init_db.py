"""Инициализация и миграция SQLite-базы данных"""
import asyncio
import aiosqlite
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS allowed_students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    group_number TEXT NOT NULL,
    password TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    full_name TEXT NOT NULL,
    group_number TEXT NOT NULL,
    unique_code TEXT UNIQUE NOT NULL,
    is_verified INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    role TEXT DEFAULT 'student',
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS meals_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id BIGINT NOT NULL,
    issued_at TIMESTAMP NOT NULL,
    issued_by BIGINT,
    method TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_meals_user_date
    ON meals_log(user_id, DATE(issued_at));
-- Архив неактивных студентов (вышли из списка: практика, академ, отчисление).
-- Хранит всё, что нужно для возврата: пароль, user_id и chat_id аккаунта.
CREATE TABLE IF NOT EXISTS archived_students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    group_number TEXT NOT NULL,
    password TEXT,
    user_id BIGINT,            -- NULL, если студент так и не зарегистрировался
    chat_id BIGINT,
    registered_at TIMESTAMP,
    archived_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_archived_name ON archived_students(full_name, group_number);
CREATE INDEX IF NOT EXISTS idx_archived_user ON archived_students(user_id);
"""

async def ensure_column(db, table: str, column: str, definition: str):
    """Добавляет колонку в таблицу, если её ещё нет"""
    cur = await db.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in await cur.fetchall()]
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        print(f"  -> Добавлена колонка {column} в таблицу {table}")

async def init_db():
    """Создание таблиц, применение миграций и настройка WAL"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.executescript(SCHEMA)
        # Умные миграции
        await ensure_column(db, "users", "code_date", "TEXT")
        # chat_id личного диалога со студентом (в MAX он НЕ равен user_id)
        await ensure_column(db, "users", "chat_id", "BIGINT")
        # дата последней успешной ежедневной рассылки QR (чтобы не слать дважды за день)
        await ensure_column(db, "users", "qr_sent_date", "TEXT")
        await ensure_column(db, "allowed_students", "password", "TEXT")
        await ensure_column(db, "allowed_students", "is_active", "INTEGER DEFAULT 1")
        await db.commit()
    print(f"✅ БД инициализирована: {DB_PATH}")

if __name__ == "__main__":
    asyncio.run(init_db())