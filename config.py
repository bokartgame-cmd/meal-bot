"""Конфигурация проекта."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "meal_bot.db"
LOGS_DIR = BASE_DIR / "logs"
BACKUP_DIR = BASE_DIR / "backups"

# === MAX ===
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "")
MAX_BOT_NAME = os.getenv("MAX_BOT_NAME", "")
# Базовый URL MAX Bot API для прямых вызовов (резервная отправка по user_id).
# По умолчанию тот же хост, на который ходит aiogram-max (см. логи).
# Новый хост https://platform-api2.max.ru требует корневой сертификат Минцифры.
MAX_API_BASE_URL = os.getenv("MAX_API_BASE_URL", "https://platform-api.max.ru").rstrip("/")

# === Общие настройки ===
# Часовой пояс бота (Владивосток, UTC+10): по нему считаются «сегодня», коды на день, отчёты и рассылка
BOT_TZ = os.getenv("BOT_TZ", "Asia/Vladivostok")
# Ежедневный бэкап БД (время по BOT_TZ); хранится BACKUP_KEEP последних копий (по одной на день)
BACKUP_ENABLED = os.getenv("BACKUP_ENABLED", "1").lower() not in ("0", "false", "no", "off")
BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "3"))
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "7"))
# Архив неактивных студентов: «старым» считается запись, пролежавшая в архиве столько дней
ARCHIVE_OLD_DAYS = int(os.getenv("ARCHIVE_OLD_DAYS", "365"))
# Жалобы и предложения: лимит обращений от одного студента в сутки и максимальная длина текста
FEEDBACK_DAILY_LIMIT = int(os.getenv("FEEDBACK_DAILY_LIMIT", "5"))
FEEDBACK_MAX_LEN = int(os.getenv("FEEDBACK_MAX_LEN", "1000"))
# Файл-пометка «кто попросил перезапуск»: после старта бот сообщает ему, что вернулся
RESTART_NOTICE_PATH = BASE_DIR / "restart_notice.json"
# === Выдача питания: кнопка студента + подтверждение сотрудником ===
# Через сколько минут права возвращаются студенту, если сотрудник не нажал «Подтвердить»
MEAL_CONFIRM_TIMEOUT_MIN = int(os.getenv("MEAL_CONFIRM_TIMEOUT_MIN", "10"))
# Как часто (сек) проверять просроченные запросы
MEAL_EXPIRE_CHECK_SECONDS = int(os.getenv("MEAL_EXPIRE_CHECK_SECONDS", "30"))

# Флаг для отладки в воскресенье (убрать в production)
DEBUG_IGNORE_WEEKDAY = os.getenv("DEBUG_IGNORE_WEEKDAY", "false").lower() in ("1", "true", "yes")