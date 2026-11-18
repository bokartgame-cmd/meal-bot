"""Диплинки MAX (ссылки-подарки)"""
import config


def generate_diplink(code: str, is_max: bool = True) -> str:
    """Диплинк MAX. Параметр is_max оставлен для совместимости со старыми вызовами."""
    bot_identifier = getattr(config, "MAX_BOT_USERNAME", "") or config.MAX_BOT_NAME
    return f"https://max.ru/{bot_identifier}?start={code}"
