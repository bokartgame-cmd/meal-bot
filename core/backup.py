"""Ежедневные резервные копии базы данных.

- Копия делается штатным API SQLite (Connection.backup): корректна при работающем боте и WAL.
- Файлы: backups/meal_bot_YYYY-MM-DD.db, по одной копии на день, хранится BACKUP_KEEP последних.
- База маленькая (SQLite-файл), копия занимает доли секунды и идёт в отдельном потоке,
  поэтому ежедневный бэкап не нагружает сервер и не мешает long polling.
- Восстановление: остановить бота и заменить data/meal_bot.db нужной копией.
"""
import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytz

import config

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^meal_bot_(\d{4}-\d{2}-\d{2})\.db$")
CHECK_INTERVAL_SECONDS = 300


def _today() -> str:
    return datetime.now(pytz.timezone(config.BOT_TZ)).strftime("%Y-%m-%d")


def backup_path(day: str) -> Path:
    return config.BACKUP_DIR / f"meal_bot_{day}.db"


def _backup_sync(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(tmp)
    try:
        src_conn.backup(dst_conn)
        dst_conn.execute("PRAGMA journal_mode=DELETE")  # копия — один самодостаточный файл
    finally:
        dst_conn.close()
        src_conn.close()
    os.replace(tmp, dst)  # атомарно: либо полная копия, либо прежняя


def rotate_backups(keep: int | None = None) -> list:
    """Оставить `keep` самых свежих копий, остальные удалить. Возвращает удалённые файлы."""
    keep = config.BACKUP_KEEP if keep is None else keep
    files = sorted(
        (p for p in config.BACKUP_DIR.glob("meal_bot_*.db") if NAME_RE.match(p.name)),
        key=lambda p: p.name,
    )
    removed = files[:-keep] if keep > 0 else files
    for p in removed:
        p.unlink(missing_ok=True)
    return removed


async def make_backup() -> Path:
    """Сделать копию на сегодня (перезаписывает сегодняшнюю, если она есть) и убрать старые."""
    dst = backup_path(_today())
    await asyncio.to_thread(_backup_sync, Path(config.DB_PATH), dst)
    removed = await asyncio.to_thread(rotate_backups)
    size_kb = dst.stat().st_size / 1024
    logger.info("💾 Бэкап БД: %s (%.0f КБ), удалено старых копий: %d", dst.name, size_kb, len(removed))
    return dst


async def ensure_backup_today() -> bool:
    """Сделать копию, только если на сегодня её ещё нет. True — копия создана."""
    if backup_path(_today()).exists():
        return False
    await make_backup()
    return True


async def backup_loop() -> None:
    """Фоновая задача: раз в сутки после BACKUP_HOUR:00 (по BOT_TZ) делает копию.
    Если бот был выключен в это время — копия создаётся сразу после запуска."""
    logger.info("💾 Ежедневный бэкап БД включён: %02d:00 (%s), хранится копий: %d, папка: %s",
                config.BACKUP_HOUR, config.BOT_TZ, config.BACKUP_KEEP, config.BACKUP_DIR)
    while True:
        try:
            now = datetime.now(pytz.timezone(config.BOT_TZ))
            if now.hour >= config.BACKUP_HOUR:
                await ensure_backup_today()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка при создании бэкапа БД")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
