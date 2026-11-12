"""Точка входа MAX-бота"""
import asyncio
import logging
from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram_max import create_bot
import config
from data.init_db import init_db
from bot.routers import user, staff, admin
from bot.daily_qr import daily_qr_loop

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    if not config.MAX_BOT_TOKEN:
        logger.error("MAX_BOT_TOKEN не задан в config.py")
        return

    # 1. Инициализация БД
    await init_db()

    # 2. Создание бота через aiogram-max
    logger.info("Подключение к MAX API...")
    bot = await create_bot(
        config.MAX_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # 3. Получаем username бота для диплинков
    me = await bot.get_me()
    config.MAX_BOT_USERNAME = me.username
    logger.info(f"✅ Username бота получен: @{me.username}")

    # 4. Запуск polling (admin первым, чтобы его callback'и перехватывались раньше)
    dp = Dispatcher()
    dp.include_routers(admin.router, staff.router, user.router)

    # 5. Фоновая ежедневная рассылка QR (polling не затрагивает)
    qr_task = None
    if config.DAILY_QR_ENABLED:
        qr_task = asyncio.create_task(daily_qr_loop(bot))

    logger.info(f"🚀 MAX-бот '{config.MAX_BOT_NAME}' успешно запущен (long-polling)")
    try:
        await dp.start_polling(bot)
    finally:
        if qr_task:
            qr_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")