"""Точка входа MAX-бота"""
import asyncio
import logging
from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram_max import create_bot
import config
from data.init_db import init_db
from bot.routers import user, staff, admin, feedback, meal
from bot.daily_qr import daily_qr_loop
from core.archive import archive_inactive_students
from bot.keyboards import ROLE_ADMIN, get_start_menu_inline
from core.backup import backup_loop, ensure_backup_today
from core.max_api import send_text_to_user
from core.restart import pop_restart_notice

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def announce_restart(bot):
    """Если бот перезапущен по кнопке администратора — сообщаем ему, что всё снова работает"""
    admin_id = pop_restart_notice()
    if not admin_id:
        return
    try:
        await send_text_to_user(bot, admin_id, "✅ Бот перезапущен и снова работает.",
                                get_start_menu_inline(ROLE_ADMIN))
    except Exception as e:
        logger.warning("Не удалось сообщить администратору %s о перезапуске: %s", admin_id, e)


async def main():
    if not config.MAX_BOT_TOKEN:
        logger.error("MAX_BOT_TOKEN не задан в config.py")
        return

    # 1. Инициализация БД
    await init_db()

    # 1.1. Страховка перед переносом неактивных студентов в архив: бэкап на сегодня (если его ещё нет)
    if config.BACKUP_ENABLED:
        try:
            await ensure_backup_today()
        except Exception:
            logger.exception("Не удалось сделать бэкап БД при запуске")
    # 1.2. Неактивные студенты (остатки старой схемы) уходят в архив; операция идемпотентна
    try:
        await archive_inactive_students()
    except Exception:
        logger.exception("Не удалось перенести неактивных студентов в архив")

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
    # meal раньше staff: его deep-link обработчики (общий QR, подарки) должны сработать до запасного личного QR
    dp.include_routers(admin.router, meal.router, staff.router, feedback.router, user.router)

    # 5. Фоновые задачи: возврат прав по таймауту, (запасная) рассылка личных QR и бэкап БД (polling не затрагивают)
    bg_tasks = [asyncio.create_task(announce_restart(bot)),
                asyncio.create_task(meal.expire_loop(bot))]  # возврат прав без подтверждения сотрудника
    if config.DAILY_QR_ENABLED:
        bg_tasks.append(asyncio.create_task(daily_qr_loop(bot)))
    if config.BACKUP_ENABLED:
        bg_tasks.append(asyncio.create_task(backup_loop()))

    logger.info(f"🚀 MAX-бот '{config.MAX_BOT_NAME}' успешно запущен (long-polling)")
    try:
        await dp.start_polling(bot)
    finally:
        for task in bg_tasks:
            task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")