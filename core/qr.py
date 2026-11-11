"""Генерация QR-кодов: QR сверху + крупный буквенный код снизу"""
import os
import segno
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import config


def generate_diplink(code: str, is_max: bool = True) -> str:
    """Диплинк MAX. Параметр is_max оставлен для совместимости со старыми вызовами."""
    bot_identifier = getattr(config, "MAX_BOT_USERNAME", "") or config.MAX_BOT_NAME
    return f"https://max.ru/{bot_identifier}?start={code}"


def generate_qr_card_bytes(payload: str, code: str, scale: int = 8) -> BytesIO:
    """
    Генерирует изображение-карточку:
    сверху QR-код (ведёт на диплинк), снизу крупным шрифтом буквенный код.
    """
    # 1. Сам QR-код из диплинка
    qr = segno.make(payload, error='M')
    qr_buffer = BytesIO()
    qr.save(qr_buffer, kind='png', scale=scale)
    qr_buffer.seek(0)

    # 2. Открываем QR через PIL
    qr_image = Image.open(qr_buffer).convert("RGBA")

    # 3. Новое изображение с белой полосой под текст
    text_height = 100
    new_width = qr_image.width
    new_height = qr_image.height + text_height

    combined_image = Image.new("RGBA", (new_width, new_height), "WHITE")
    combined_image.paste(qr_image, (0, 0))

    # 4. Рисуем буквенный код крупным шрифтом по центру нижней полосы
    draw = ImageDraw.Draw(combined_image)
    try:
        font_path = "arial.ttf" if os.name == 'nt' else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = ImageFont.truetype(font_path, 50)
    except IOError:
        font = ImageFont.load_default()

    try:
        bbox = draw.textbbox((0, 0), code, font=font)
        text_width = bbox[2] - bbox[0]
        text_height_actual = bbox[3] - bbox[1]
    except AttributeError:
        text_width, text_height_actual = draw.textsize(code, font=font)

    x = (new_width - text_width) // 2
    y = qr_image.height + (text_height - text_height_actual) // 2 + 5
    draw.text((x, y), code, fill="BLACK", font=font)

    # 5. Итоговое изображение в память
    final_buffer = BytesIO()
    combined_image.save(final_buffer, format="PNG")
    final_buffer.seek(0)
    return final_buffer