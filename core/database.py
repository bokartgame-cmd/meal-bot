"""Асинхронная обёртка над SQLite"""
import aiosqlite
from contextlib import asynccontextmanager
from config import DB_PATH

@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
