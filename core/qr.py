"""Диплинки MAX (ссылки-подарки)"""
import config


def generate_diplink(code: str, is_max: bool = True) -> str:
    """Диплинк MAX. Параметр is_max оставлен для совместимости со старыми вызовами.
    
    Возвращает ссылку, обёрнутую в HTML-тег <a>, чтобы избежать проблем с парсингом
    специальных символов (например, _) в username бота при parse_mode=HTML.
    """
    bot_identifier = getattr(config, "MAX_BOT_USERNAME", "") or config.MAX_BOT_NAME
    url = f"https://max.ru/{bot_identifier}?start={code}"
    # Оборачиваем в HTML-тег, чтобы URL не парсился как текст с форматированием
    # и чтобы символы вроде _ в username не интерпретировались как markdown/html
    return f'<a href="{url}">{url}</a>'

