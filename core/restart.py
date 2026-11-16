"""Перезапуск бота по кнопке администратора.

Как это работает
- run.py запускает бота дочерним процессом (режим «супервизора», переменная MEAL_BOT_SUPERVISED=1).
  По кнопке бот завершается с кодом RESTART_EXIT_CODE, а run.py запускает его заново:
  код читается с диска заново, поэтому после `git pull` перезапуск применяет обновление.
  Консоль PyCharm остаётся подключённой, кнопка «Стоп» останавливает и супервизор, и бота.
- Без супервизора (например, `python -m bot.main` на Linux/macOS) процесс заменяется на месте
  через os.execv. На Windows без run.py перезапуск недоступен — кнопка об этом сообщит.

Защита от «перезапуска по кругу»: в кнопку подтверждения зашита метка времени. Нажатие,
созданное до старта текущего процесса (повторно доставленное MAX после рестарта) или
старше TOKEN_TTL_SECONDS, отвергается.
"""
import json
import logging
import os
import sys
import time

import config

logger = logging.getLogger(__name__)

RESTART_EXIT_CODE = 75  # EX_TEMPFAIL: «запустите меня снова»
SUPERVISED_ENV = "MEAL_BOT_SUPERVISED"
TOKEN_TTL_SECONDS = 300
NOTICE_TTL_SECONDS = 180

STARTED_AT = time.time()  # модуль импортируется при старте процесса


def is_supervised() -> bool:
    return os.environ.get(SUPERVISED_ENV) == "1"


def restart_supported() -> bool:
    return is_supervised() or os.name != "nt"


def make_token(prefix: str) -> str:
    """Данные кнопки подтверждения: «prefix:метка_времени»"""
    return f"{prefix}:{int(time.time())}"


def token_state(data: str) -> str:
    """'ok' — можно выполнять; 'stale' — устарело или создано до старта процесса; 'invalid' — мусор"""
    try:
        ts = int(data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return "invalid"
    if ts + 1 < STARTED_AT or time.time() - ts > TOKEN_TTL_SECONDS:
        return "stale"
    return "ok"


def save_restart_notice(user_id: int) -> None:
    """Запомнить, кому сообщить, что бот вернулся"""
    try:
        config.RESTART_NOTICE_PATH.write_text(
            json.dumps({"user_id": int(user_id), "ts": time.time()}), encoding="utf-8"
        )
    except OSError as e:
        logger.warning("Не удалось записать метку перезапуска: %s", e)


def pop_restart_notice() -> int | None:
    """Кому сообщить о завершённом перезапуске (и удалить метку)"""
    path = config.RESTART_NOTICE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    if time.time() - float(data.get("ts", 0)) > NOTICE_TTL_SECONDS:
        return None
    return int(data["user_id"])


def restart_process() -> None:
    """Завершить текущий процесс так, чтобы бот запустился заново. Не возвращается."""
    logger.warning("🔄 Перезапуск бота (supervised=%s)", is_supervised())
    logging.shutdown()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    if is_supervised():
        os._exit(RESTART_EXIT_CODE)
    if os.name == "nt":
        raise RuntimeError("На Windows перезапуск работает только при запуске через run.py")
    os.execv(sys.executable, [sys.executable, *sys.argv])
