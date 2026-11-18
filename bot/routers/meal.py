"""MAX-бот: выдача питания — кнопка студента, подтверждение студента и сотрудника, подарки.

Путь одной выдачи:
  сотрудник «Начать выдачу» -> студент нажимает «Получить обед»
  -> студент «Подтвердить» (права резервируются) -> сотруднику «Успешно! Иванов И., 28-143.
  Обедов к выдаче: N» -> сотрудник «Подтвердить» (запись в журнал, студенту «Обед получен»).
Без подтверждения сотрудника права возвращаются студенту по таймауту (expire_loop).
"""
import asyncio
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.enums import ParseMode

import config
from bot.fsm import GiftAccept
from bot.keyboards import (
    CB_GIFT_ACCEPT,
    CB_GIFT_NEW,
    CB_GIFT_REVOKE_PREFIX,
    CB_MEAL_CONFIRM,
    CB_MEAL_GET,
    CB_MEAL_GIVE_PREFIX,
    CB_STAFF_START,
    STUDENT_MEAL_RECEIVED_TEXT,
    STUDENT_RATE_PROMPT,
    get_gift_revoke_inline,
    get_rating_inline,
    get_staff_confirm_inline,
    get_start_menu_inline,
    get_student_confirm_inline,
)
from bot.routers.user import RememberChatMiddleware
from core import meals
from core.feedback import neutralize
from core.max_api import delete_message, edit_message_text, send_text_to_user
from core.qr import generate_diplink
from core.services import get_user_info

logger = logging.getLogger(__name__)
router = Router(name="max_meal")
# chat_id личного диалога нужен для уведомлений (сотруднику — в первую очередь)
router.message.outer_middleware(RememberChatMiddleware())
router.callback_query.outer_middleware(RememberChatMiddleware())

TEXT_NO_SESSION = "Выдача сейчас не ведётся."
TEXT_NO_RIGHTS = (
    "❌ У вас нет права на обед сегодня: оно уже использовано или подарено. "
    "Новое право появится завтра."
)
TEXT_NOT_STUDENT = "⚠️ Получать обеды могут только зарегистрированные студенты. Пройдите регистрацию: /start"
TEXT_PENDING = "⏳ Ваш запрос уже отправлен сотруднику. Дождитесь подтверждения выдачи."
TEXT_STAFF_UNAVAILABLE = "⚠️ Выдача сейчас недоступна, попробуйте ещё раз."
TEXT_STAFF_ONLY = "⛔ Эта функция только для сотрудников столовой."

GIFT_DENIED_TEXTS = {
    "invalid": "❌ Ссылка недействительна.",
    "used": "⚠️ Эта ссылка уже использована: подарок можно принять только один раз.",
    "revoked": "⚠️ Даритель забрал подарок обратно.",
    "expired": "⌛ Подарок действовал только до конца суток, ссылка устарела.",
    "own": "ℹ️ Это ваша собственная ссылка. Отправьте её другу.",
}


def _obeds(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} обед"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} обеда"
    return f"{n} обедов"


async def _is_staff(user_id: int) -> dict | None:
    info = await get_user_info(user_id)
    return info if info and info.get("role") in ("staff", "admin") else None


async def _notify(bot, user_id: int, text: str, markup=None) -> dict | None:
    """Уведомление пользователю; сбой доставки не должен ломать основной сценарий"""
    try:
        return await send_text_to_user(bot, int(user_id), text, markup)
    except Exception as e:
        logger.warning("Не удалось уведомить пользователя %s: %s: %s", user_id, type(e).__name__, e)
        return None


# ====================
# СОТРУДНИК: НАЧАТЬ ВЫДАЧУ
# ====================

async def _send_request_to_staff(bot, request_id: int, staff_id: int, name: str, count: int) -> None:
    """Сообщение сотруднику с кнопкой «Подтвердить». При сбое доставки бросает исключение.
    После отправки удаляет у сотрудника старые уже подтверждённые сообщения о выдаче."""
    text = f"Успешно! {neutralize(name)}. Обедов к выдаче: {count}"
    ref = await send_text_to_user(bot, staff_id, text, get_staff_confirm_inline(request_id))
    await meals.attach_staff_message(request_id, ref.get("chat_id"), ref.get("message_id"))
    for old in await meals.pop_confirmed_messages(staff_id, keep_request_id=request_id):
        await delete_message(bot, old["chat_id"], old["message_id"])


@router.callback_query(F.data == CB_STAFF_START)
async def cb_start_issuance(callback: CallbackQuery):
    """Кнопка «Начать выдачу»: с этого момента и до конца суток запросы приходят этому сотруднику"""
    await callback.answer()
    uid = callback.from_user.id
    if not await _is_staff(uid):
        await callback.message.answer(TEXT_STAFF_ONLY)
        return

    result = await meals.start_issuance(uid)
    info = await get_user_info(uid)
    await callback.message.answer(
        "✅ Выдача начата. Запросы студентов будут приходить вам до конца суток.",
        reply_markup=get_start_menu_inline(info["role"] if info else "staff"),
    )

    previous = result.get("previous_staff_id")
    if previous:
        # Передача выдачи: прежнему сотруднику — уведомление, неподтверждённые запросы — новому
        await _notify(callback.bot, previous,
                      "ℹ️ Выдачу начал другой сотрудник. Новые запросы студентов будут приходить ему.")
        for req in await meals.reassign_pending(uid):
            await delete_message(callback.bot, req.get("staff_chat_id"), req.get("staff_message_id"))
            try:
                await _send_request_to_staff(callback.bot, req["id"], uid, req["name"], req["count"])
            except Exception as e:
                logger.warning("Не удалось переслать запрос %s новому сотруднику: %s", req["id"], e)


# ====================
# СТУДЕНТ: КНОПКА «ПОЛУЧИТЬ ОБЕД»
# ====================

@router.callback_query(F.data == CB_MEAL_GET)
async def cb_meal_get(callback: CallbackQuery):
    """Студент нажал «Получить обед»: проверяем выдачу и права, просим подтвердить"""
    await callback.answer()
    uid = callback.from_user.id
    if await _is_staff(uid):
        await callback.message.answer("ℹ️ Получать обеды могут только студенты. "
                                      "Чтобы вести выдачу, нажмите «Начать выдачу».")
        return

    check = await meals.check_scan(uid)
    status = check["status"]
    if status == "not_student":
        await callback.message.answer(TEXT_NOT_STUDENT)
    elif status == "no_session":
        await callback.message.answer(TEXT_NO_SESSION)
    elif status == "pending":
        await callback.message.answer(TEXT_PENDING)
    elif status == "no_rights":
        await callback.message.answer(TEXT_NO_RIGHTS)   # сотруднику сообщение не уходит
    else:
        await callback.message.answer(
            f"Обедов к получению: {check['rights']}.\n\n"
            "Нажмите «Подтвердить», когда будете у раздачи. Если не нажмёте, ничего не спишется.",
            reply_markup=get_student_confirm_inline(),
        )


@router.callback_query(F.data == CB_MEAL_CONFIRM)
async def cb_student_confirm(callback: CallbackQuery):
    """Студент подтвердил запрос: права резервируются, сотруднику уходит сообщение"""
    await callback.answer()
    uid = callback.from_user.id
    res = await meals.reserve_request(uid)
    status = res["status"]

    if status != "ok":
        text = {
            "not_student": TEXT_NOT_STUDENT,
            "no_session": TEXT_NO_SESSION,
            "pending": TEXT_PENDING,
            "no_rights": TEXT_NO_RIGHTS,
        }.get(status, TEXT_STAFF_UNAVAILABLE)
        await _safe_edit(callback, text)
        return

    try:
        await _send_request_to_staff(callback.bot, res["request_id"], res["staff_id"],
                                     res["name"], res["count"])
    except Exception as e:
        # сообщение не доставлено: права не списываем, студент может попробовать ещё раз
        logger.error("Не удалось отправить запрос %s сотруднику %s: %s: %s",
                     res["request_id"], res["staff_id"], type(e).__name__, e)
        await meals.cancel_request(res["request_id"])
        await _safe_edit(callback, TEXT_STAFF_UNAVAILABLE, get_student_confirm_inline())
        return

    await _safe_edit(
        callback,
        f"⏳ Запрос отправлен сотруднику (к выдаче: {_obeds(res['count'])}). "
        "Ожидайте — после подтверждения выдачи придёт уведомление.",
    )


async def _reply(callback: CallbackQuery, text: str) -> None:
    """Ответ на нажатие кнопки обычным сообщением (видно всегда, в отличие от toast)"""
    await callback.answer()
    await callback.message.answer(text)


async def _safe_edit(callback: CallbackQuery, text: str, markup=None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception as e:
        logger.debug("Не удалось изменить сообщение, отправляю новое: %s", e)
        await callback.message.answer(text, reply_markup=markup)


# ====================
# СОТРУДНИК: ПОДТВЕРЖДЕНИЕ ВЫДАЧИ
# ====================

@router.callback_query(F.data.startswith(CB_MEAL_GIVE_PREFIX))
async def cb_staff_confirm(callback: CallbackQuery):
    """Сотрудник выдал обеды и нажал «Подтвердить»: запись в журнал, студенту — «Обед получен»"""
    try:
        request_id = int(callback.data[len(CB_MEAL_GIVE_PREFIX):])
    except ValueError:
        await callback.answer()
        return

    res = await meals.confirm_issuance(request_id, callback.from_user.id)
    status = res["status"]

    if status == "forbidden":
        await _reply(callback, "⛔ Сейчас выдачу ведёт другой сотрудник.")
        return
    if status == "not_found":
        await _reply(callback, "Запрос не найден.")
        return
    if status == "done":
        await _reply(callback, "ℹ️ Выдача уже подтверждена.")
        return
    if status == "expired":
        await callback.answer()
        await _safe_edit(callback, "⌛ Время подтверждения вышло, права возвращены студенту. "
                                   "Пусть студент нажмёт «Получить обед» ещё раз.")
        return

    await callback.answer()
    student = await get_user_info(res["user_id"])
    who = meals.short_name(student["full_name"], student["group_number"]) if student else "студент"
    await _safe_edit(callback, f"✅ Выдано: {neutralize(who)}. Обедов: {res['count']}")

    # студенту: «Обед получен» + просьба оценить (оценка привязана к первой записи журнала)
    text = f"{STUDENT_MEAL_RECEIVED_TEXT}"
    if res["count"] > 1:
        text = f"✅ Обед получен! Выдано: {_obeds(res['count'])}. Приятного аппетита."
    await _notify(callback.bot, res["user_id"], f"{text}\n\n{STUDENT_RATE_PROMPT}",
                  get_rating_inline(int(res["meal_ids"][0])))


# ====================
# ПОДАРКИ
# ====================

@router.callback_query(F.data == CB_GIFT_NEW)
async def cb_gift_new(callback: CallbackQuery):
    """«Угостить друга»: право списывается сразу, создаётся одноразовая ссылка"""
    uid = callback.from_user.id
    res = await meals.create_gift(uid)
    if res["status"] == "not_student":
        # раньше был всплывающий toast, который в MAX легко не заметить, — отвечаем сообщением
        await _reply(callback, "ℹ️ Угощать друзей могут только зарегистрированные студенты."
                               if await _is_staff(uid) else TEXT_NOT_STUDENT)
        return
    if res["status"] == "no_rights":
        await _reply(callback, "🎁 Подарить нечего: право на обед сегодня уже использовано, подарено "
                               "или ждёт подтверждения выдачи. Новое право появится завтра.")
        return
    await callback.answer()
    link = generate_diplink(f"{meals.GIFT_PREFIX}{res['token']}", is_max=True)
    code = _pretty_code(res["token"])
    logger.info("Подарок: студент %s создал ссылку %s", uid, link)
    await callback.message.answer(
        "🎁 Ссылка для друга:\n"
        f"{link}\n\n"
        f"Если ссылка не открывается, друг может нажать «🎟 Ввести код подарка» и отправить код:\n{code}\n\n"
        "Перешлите это другу: он получит ваше право на обед. "
        "Подарок можно принять один раз и только до конца суток.\n"
        "Ваше право на обед передано в подарок. Пока друг не открыл ссылку, "
        "вы можете забрать его обратно.",
        parse_mode=ParseMode.DISABLED,
        reply_markup=get_gift_revoke_inline(res["token"]),
    )


@router.callback_query(F.data.startswith(CB_GIFT_REVOKE_PREFIX))
async def cb_gift_revoke(callback: CallbackQuery):
    await callback.answer()
    token = callback.data[len(CB_GIFT_REVOKE_PREFIX):]
    res = await meals.revoke_gift(token, callback.from_user.id)
    text = {
        "ok": "↩️ Подарок отозван: право на обед вернулось к вам. Ссылка больше не работает.",
        "not_active": "⚠️ Ссылка уже использована другом или отозвана раньше.",
        "expired": "⌛ Сутки закончились: право сгорело.",
    }.get(res["status"], "❌ Подарок не найден.")
    await _safe_edit(callback, text)


def _pretty_code(token: str) -> str:
    """16 hex-символов группами по 4: ab12-cd34-ef56-7890"""
    return "-".join(token[i:i + 4] for i in range(0, len(token), 4))


def _parse_code(text: str) -> str:
    """Код подарка из текста пользователя: без дефисов, пробелов и регистра"""
    return re.sub(r"[^0-9a-f]", "", (text or "").lower())


async def _accept_gift(message: Message, uid: int, token: str) -> None:
    """Друг принимает подарок (по ссылке или по коду): право переносится ему"""
    if await _is_staff(uid):
        await message.answer("ℹ️ Подарок может принять только студент.")
        return
    res = await meals.redeem_gift(token, uid)
    status = res["status"]
    if status == "not_student":
        await message.answer(
            "Чтобы принять подарок, сначала пройдите регистрацию в боте (/start), "
            "а потом откройте ссылку или введите код ещё раз."
        )
    elif status != "ok":
        await message.answer(GIFT_DENIED_TEXTS.get(status, GIFT_DENIED_TEXTS["invalid"]))
    else:
        await message.answer(
            f"🎁 Вам подарили обед! Обедов на сегодня: {res['rights']}.\n"
            "Нажмите «Получить обед», когда будете у раздачи.",
            reply_markup=get_start_menu_inline("student"),
        )
        await _notify(message.bot, res["giver_id"], "🎉 Друг принял ваш подарок.")


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(meals.GIFT_PREFIX)))
async def on_gift_link(message: Message, command: CommandObject, state: FSMContext):
    """Друг открыл ссылку-подарок"""
    await state.clear()
    token = (command.args or "")[len(meals.GIFT_PREFIX):].lstrip("_")  # lstrip: старые ссылки gift_<токен>
    logger.info("Подарок: пользователь %s открыл ссылку (payload=%r)", message.from_user.id, command.args)
    await _accept_gift(message, message.from_user.id, token)


@router.callback_query(F.data == CB_GIFT_ACCEPT)
async def cb_gift_accept(callback: CallbackQuery, state: FSMContext):
    """Запасной путь: код подарка вводится вручную, если ссылка не открывается"""
    await callback.answer()
    await state.set_state(GiftAccept.waiting_code)
    await callback.message.answer("🎟 Пришлите код подарка (его прислал друг), например: ab12-cd34-ef56-7890")


@router.message(Command("gift"))
async def cmd_gift(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    token = _parse_code(command.args or "")
    if not token:
        await state.set_state(GiftAccept.waiting_code)
        await message.answer("🎟 Пришлите код подарка (его прислал друг), например: ab12-cd34-ef56-7890")
        return
    await _accept_gift(message, message.from_user.id, token)


@router.message(GiftAccept.waiting_code, F.text, ~F.text.startswith("/"))
async def on_gift_code(message: Message, state: FSMContext):
    token = _parse_code(message.text)
    await state.clear()
    if len(token) != 16:
        await message.answer("❌ Код не похож на код подарка. Нажмите «🎟 Ввести код подарка» и попробуйте ещё раз.",
                             reply_markup=get_start_menu_inline("student"))
        return
    await _accept_gift(message, message.from_user.id, token)


# ====================
# ФОН: ВОЗВРАТ ПРАВ ПО ТАЙМАУТУ
# ====================

async def process_expired(bot) -> int:
    """Одна проверка: просроченные запросы -> права вернулись, студент и сотрудник уведомлены"""
    expired = await meals.expire_pending()
    for req in expired:
        if req["restored"]:
            text = ("⌛ Сотрудник не подтвердил выдачу вовремя. Право на обед вернулось к вам — "
                    "нажмите «Получить обед» ещё раз, когда будете у раздачи.")
        else:
            text = "⌛ Сутки закончились, а выдача не была подтверждена. Право на обед сгорело."
        await _notify(bot, req["user_id"], text)
        stale = f"⌛ Запрос истёк без подтверждения (к выдаче было: {_obeds(req['count'])}). Права возвращены студенту."
        if not await edit_message_text(bot, req.get("staff_chat_id"), req.get("staff_message_id"), stale):
            await delete_message(bot, req.get("staff_chat_id"), req.get("staff_message_id"))
    return len(expired)


async def expire_loop(bot) -> None:
    """Фоновая задача (bot/main.py): раз в MEAL_EXPIRE_CHECK_SECONDS возвращает просроченные права"""
    logger.info("⏱ Возврат прав по таймауту: %d мин, проверка каждые %d с",
                config.MEAL_CONFIRM_TIMEOUT_MIN, config.MEAL_EXPIRE_CHECK_SECONDS)
    while True:
        try:
            n = await process_expired(bot)
            if n:
                logger.info("Просрочено запросов на выдачу: %d", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в цикле возврата прав")
        await asyncio.sleep(config.MEAL_EXPIRE_CHECK_SECONDS)
