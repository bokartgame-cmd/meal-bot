"""Конфигурация проекта."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "meal_bot.db"
LOGS_DIR = BASE_DIR / "logs"

# === MAX ===
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "")
MAX_BOT_NAME = os.getenv("MAX_BOT_NAME", "")
# Базовый URL MAX Bot API для прямых вызовов (резервная отправка по user_id).
# По умолчанию тот же хост, на который ходит aiogram-max (см. логи).
# Новый хост https://platform-api2.max.ru требует корневой сертификат Минцифры.
MAX_API_BASE_URL = os.getenv("MAX_API_BASE_URL", "https://platform-api.max.ru").rstrip("/")

# === Общие настройки ===
MOSCOW_TZ = "Europe/Moscow"
# Ежедневная автоматическая рассылка QR-кодов студентам (время по MOSCOW_TZ)
DAILY_QR_ENABLED = os.getenv("DAILY_QR_ENABLED", "1").lower() not in ("0", "false", "no", "off")
DAILY_QR_HOUR = int(os.getenv("DAILY_QR_HOUR", "6"))
DAILY_QR_MINUTE = int(os.getenv("DAILY_QR_MINUTE", "0"))
ALLOWED_STAFF_ATTEMPTS = 10
STAFF_COOLDOWN_SECONDS = 60
QR_PAYLOAD_MAX_LEN = 128

# Флаг для отладки в воскресенье (убрать в production)
DEBUG_IGNORE_WEEKDAY = os.getenv("DEBUG_IGNORE_WEEKDAY", "false").lower() in ("1", "true", "yes")