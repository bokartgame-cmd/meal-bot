"""core/notifications.py - Уведомления студентов о выдаче питания"""
import logging
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Глобальный словарь для хранения ID последних сообщений о выдаче
# Ключ: student_id (int), Значение: message_id (int) в чате студента
student_messages = {}

async def notify_student_with_cleanup(message: Message, result: dict):
    """
    Уведомление студента об успешной выдаче с удалением предыдущего сообщения.
    """
    if result.get("status") != "success":
        return

    student_id = result.get("student_user_id")
    if not student_id:
        return

    # 1. Удаляем старое сообщение о получении у студента (если оно есть)
    if student_id in student_messages:
        try:
            await message.bot.delete_message(
                chat_id=student_id,
                message_id=student_messages[student_id]
            )
        except Exception as e:
            logger.debug("Не удалось удалить старое сообщение у студента %s: %s", student_id, e)

    # 2. Отправляем новое сообщение
    try:
        sent_message = await message.bot.send_message(
            chat_id=student_id,
            text="✅ Успешное получение! Приятного аппетита."
        )
        # Сохраняем ID нового сообщения для следующей итерации
        student_messages[student_id] = sent_message.message_id
    except Exception as e:
        # 🔒 Ловим ВСЕ ошибки (включая MaxApiError 404), чтобы не прерывать выдачу сотруднику.
        # Это абсолютно нормальная ситуация, если студент ещё не запускал бота (/start).
        logger.warning("Не удалось уведомить студента %s (возможно, он не запускал бота): %s", student_id, e)