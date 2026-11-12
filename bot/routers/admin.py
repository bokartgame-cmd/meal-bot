"""MAX-бот: Обработчики для администратора (статистика и импорт)"""
import logging
import tempfile
from datetime import datetime
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.fsm import AdminStats, AdminImport
from bot.keyboards import (
    BTN_CANCEL,
    CB_ADMIN_STATS,
    CB_ADMIN_ARCHIVE,
    CB_ADMIN_IMPORT,
    CB_ARCHIVE_CANCEL,
    CB_ARCHIVE_CLEAR_ALL,
    CB_ARCHIVE_CLEAR_OLD,
    CB_ARCHIVE_CONFIRM_ALL,
    CB_ARCHIVE_CONFIRM_OLD,
    CB_CANCEL,
    CB_STATS_CUSTOM,
    CB_STATS_MONTH,
    CB_STATS_WEEK,
    CB_STATS_YEAR,
    ROLE_ADMIN,
    get_archive_confirm_inline,
    get_archive_menu_inline,
    get_cancel_inline,
    get_start_menu_inline,
    get_stats_period_inline,
)
import config
from core.archive import clear_archive, count_archived
from core.excel import build_stats_excel, build_passwords_excel
from core.importer import import_students_from_file
from core.max_api import send_text_to_user
from core.services import (
    VALID_ROLES,
    get_stats_for_period,
    get_user_info,
    set_user_role,
    parse_date,
    period_last_month,
    period_last_week,
    period_last_year,
)

logger = logging.getLogger(__name__)
router = Router(name="max_admin")

async def _require_admin(callback_or_message, user_id: int) -> bool:
    info = await get_user_info(user_id)
    if not info or info.get("role") != ROLE_ADMIN:
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.answer("⛔ Только для администратора.", show_alert=True)
        else:
            await callback_or_message.answer("⛔ Только для администратора.")
        return False
    return True

async def send_stats(message: Message, from_date: str, to_date: str):
    """Формирование и отправка Excel-файла"""
    stats = await get_stats_for_period(from_date, to_date)
    buffer = build_stats_excel(from_date, to_date, stats)
    filename = f"stats_{from_date}_{to_date}.xlsx"
    file = BufferedInputFile(buffer.read(), filename=filename)

    caption = (
        f"📊 Статистика за период: с {from_date} по {to_date}\n"
        f"🍽 Всего выдано обедов: {stats['total_issued']}\n"
        f"✅ Активных получили ≥1: {stats['active_got']} из {stats['active_total']}\n"
        f"❌ Активных не получили ни одного: {stats['active_not_got']}"
    )

    # 🔧 ИСПРАВЛЕНИЕ: Разделяем отправку текста и документа, чтобы избежать 400 Bad Request от MAX API
    await message.answer(caption)
    await message.answer_document(document=file)

# ====================
# ВЫДАЧА РОЛЕЙ: /set_role <id> <role>
# ====================
# Хендлер стоит ПЕРВЫМ в роутере: команда должна работать в любом FSM-состоянии
# (иначе её перехватит текстовый хендлер состояния, например ввода даты).

ROLE_LABELS = {"student": "студент", "staff": "сотрудник столовой", "admin": "администратор"}
SET_ROLE_USAGE = (
    "ℹ️ Использование: /set_role <id> <роль>\n"
    "Роли: student, staff, admin\n"
    "Пример: /set_role 123456789 staff\n\n"
    "ID пользователя можно узнать командой /id (пользователь отправляет её боту сам)."
)


@router.message(Command("set_role"))
async def cmd_set_role(message: Message, command: CommandObject):
    """Назначение роли пользователю администратором"""
    if not await _require_admin(message, message.from_user.id):
        return

    args = (command.args or "").split()
    if len(args) != 2:
        await message.answer(SET_ROLE_USAGE)
        return

    raw_id, role = args[0], args[1].lower()
    if not raw_id.isdigit():
        await message.answer(f"❌ ID должен быть числом, получено: {raw_id}\n\n{SET_ROLE_USAGE}")
        return
    if role not in VALID_ROLES:
        await message.answer(f"❌ Неизвестная роль: {args[1]}\n\n{SET_ROLE_USAGE}")
        return

    target_id = int(raw_id)

    # Защита от случайного снятия прав с себя (можно остаться без администраторов)
    if target_id == message.from_user.id and role != "admin":
        await message.answer("⛔ Нельзя понизить собственную роль. Попросите другого администратора.")
        return

    try:
        result = await set_user_role(target_id, role)
    except Exception as e:
        logger.exception("Ошибка при назначении роли")
        await message.answer(f"❌ Не удалось назначить роль: {e}")
        return

    if result["status"] == "not_found":
        await message.answer(
            f"❌ Пользователь {target_id} не найден. Студент появляется в базе после регистрации "
            f"в боте. Для staff/admin запись создаётся автоматически."
        )
        return

    if result["status"] == "created":
        report = f"✅ Создан пользователь {target_id} с ролью «{role}» ({ROLE_LABELS[role]})."
    else:
        report = f"✅ Пользователю {target_id} роль изменена: {result['old_role']} → {role}."
    logger.info("Админ %s назначил роль %s пользователю %s", message.from_user.id, role, target_id)

    # Сообщаем самому пользователю; ошибка здесь не отменяет назначение роли
    try:
        await send_text_to_user(
            message.bot,
            target_id,
            f"🔔 Вам назначена роль: {ROLE_LABELS[role]}. Отправьте /start, чтобы открыть меню.",
        )
        report += "\n📨 Пользователь уведомлён."
    except Exception as e:
        logger.warning("Не удалось уведомить пользователя %s о новой роли: %s", target_id, e)
        report += "\n⚠️ Уведомить пользователя не удалось (возможно, он ещё не запускал бота)."

    await message.answer(report)


# ====================
# АРХИВ НЕАКТИВНЫХ СТУДЕНТОВ
# ====================

def _archive_text(total: int, old: int) -> str:
    return (
        "🗄 Архив студентов\n\n"
        "Сюда автоматически попадают студенты, которых нет в загруженном списке "
        "(практика, академический отпуск, отчисление). Если студент снова появится "
        "в списке при импорте, он вернётся из архива со своим паролем и аккаунтом.\n\n"
        f"В архиве сейчас: {total}\n"
        f"Из них пролежало дольше {config.ARCHIVE_OLD_DAYS} дн.: {old}"
    )


async def _show_archive(target: Message):
    total = await count_archived()
    old = await count_archived(config.ARCHIVE_OLD_DAYS)
    await target.answer(
        _archive_text(total, old),
        reply_markup=get_archive_menu_inline(total, old, config.ARCHIVE_OLD_DAYS),
    )


async def _delete_quietly(message: Message):
    try:
        await message.delete()
    except Exception:
        pass


@router.message(Command("archive"))
async def cmd_archive(message: Message, state: FSMContext):
    if not await _require_admin(message, message.from_user.id):
        return
    await state.clear()
    await _show_archive(message)


@router.callback_query(F.data == CB_ADMIN_ARCHIVE)
async def cb_admin_archive(callback: CallbackQuery, state: FSMContext):
    if not await _require_admin(callback, callback.from_user.id):
        return
    await callback.answer()
    await state.clear()
    await _delete_quietly(callback.message)
    await _show_archive(callback.message)


@router.callback_query(F.data.in_({CB_ARCHIVE_CLEAR_OLD, CB_ARCHIVE_CLEAR_ALL}))
async def cb_archive_clear_ask(callback: CallbackQuery):
    """Шаг 1: запрос подтверждения (удаление необратимо)"""
    if not await _require_admin(callback, callback.from_user.id):
        return
    await callback.answer()
    only_old = callback.data == CB_ARCHIVE_CLEAR_OLD
    count = await count_archived(config.ARCHIVE_OLD_DAYS if only_old else None)
    if not count:
        await callback.message.answer("ℹ️ Удалять нечего.")
        return
    scope = f"пролежавших в архиве дольше {config.ARCHIVE_OLD_DAYS} дн." if only_old else "ВСЕХ студентов"
    await _delete_quietly(callback.message)
    await callback.message.answer(
        f"⚠️ Будет безвозвратно удалено {count} студ. ({scope}).\n\n"
        "Их пароли и привязка аккаунтов пропадут: если кто-то из них вернётся, "
        "ему выдадут новый пароль и придётся зарегистрироваться заново.\n"
        "История выдач в общих итогах статистики сохранится, но без имён.\n\n"
        "Подтвердить удаление?",
        reply_markup=get_archive_confirm_inline(
            CB_ARCHIVE_CONFIRM_OLD if only_old else CB_ARCHIVE_CONFIRM_ALL
        ),
    )


@router.callback_query(F.data.in_({CB_ARCHIVE_CONFIRM_OLD, CB_ARCHIVE_CONFIRM_ALL}))
async def cb_archive_clear_confirm(callback: CallbackQuery):
    """Шаг 2: удаление после подтверждения"""
    if not await _require_admin(callback, callback.from_user.id):
        return
    await callback.answer()
    only_old = callback.data == CB_ARCHIVE_CONFIRM_OLD
    deleted = await clear_archive(config.ARCHIVE_OLD_DAYS if only_old else None)
    logger.warning("Админ %s очистил архив студентов (%s): удалено %d",
                   callback.from_user.id, "старше %d дн." % config.ARCHIVE_OLD_DAYS if only_old else "весь", deleted)
    await _delete_quietly(callback.message)
    await callback.message.answer(f"✅ Из архива удалено студентов: {deleted}.")
    await _show_archive(callback.message)


@router.callback_query(F.data == CB_ARCHIVE_CANCEL)
async def cb_archive_back(callback: CallbackQuery):
    await callback.answer()
    info = await get_user_info(callback.from_user.id)
    await _delete_quietly(callback.message)
    await callback.message.answer(
        "👋 Выберите действие:",
        reply_markup=get_start_menu_inline(info["role"] if info else "admin"),
    )


# ====================
# СТАТИСТИКА
# ====================

@router.callback_query(F.data == CB_ADMIN_STATS)
async def cb_admin_stats(callback: CallbackQuery, state: FSMContext):
    if not await _require_admin(callback, callback.from_user.id):
        return
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "📊 Выберите период для выгрузки статистики:",
        reply_markup=get_stats_period_inline(),
    )
    await state.set_state(AdminStats.choosing_period)
    await callback.answer()

@router.callback_query(
    AdminStats.choosing_period,
    F.data.in_({CB_STATS_WEEK, CB_STATS_MONTH, CB_STATS_YEAR}),
)
async def cb_stats_preset(callback: CallbackQuery, state: FSMContext):
    if not await _require_admin(callback, callback.from_user.id):
        return

    if callback.data == CB_STATS_WEEK:
        from_d, to_d = period_last_week()
        label = "неделю"
    elif callback.data == CB_STATS_MONTH:
        from_d, to_d = period_last_month()
        label = "месяц"
    else:
        from_d, to_d = period_last_year()
        label = "год"

    await state.clear()
    await callback.message.edit_text(f"⏳ Формирую отчёт за {label} ({from_d} — {to_d})...")

    await send_stats(callback.message, from_d, to_d)

    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение-заглушку: %s", e)

    info = await get_user_info(callback.from_user.id)
    await callback.message.answer(
        "👋 Выберите действие:",
        reply_markup=get_start_menu_inline(info["role"] if info else "admin"),
    )
    await callback.answer()

@router.callback_query(AdminStats.choosing_period, F.data == CB_STATS_CUSTOM)
async def cb_stats_custom_from(callback: CallbackQuery, state: FSMContext):
    if not await _require_admin(callback, callback.from_user.id):
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "📅 Введите дату начала периода в формате ГГГГ-ММ-ДД\n(например: 2026-10-01)",
        reply_markup=get_cancel_inline(),
    )
    await state.set_state(AdminStats.custom_from)
    await callback.answer()

@router.message(AdminStats.custom_from, F.text)
async def process_custom_from(message: Message, state: FSMContext):
    d = parse_date(message.text)
    if not d:
        await message.answer("❌ Неверный формат. Используйте ГГГГ-ММ-ДД.")
        return
    await state.update_data(custom_from=d)
    await message.answer(
        f"✅ Начало периода: {d}\nТеперь введите дату окончания периода в формате ГГГГ-ММ-ДД:",
        reply_markup=get_cancel_inline(),
    )
    await state.set_state(AdminStats.custom_to)

@router.message(AdminStats.custom_to, F.text)
async def process_custom_to(message: Message, state: FSMContext):
    d = parse_date(message.text)
    if not d:
        await message.answer("❌ Неверный формат. Используйте ГГГГ-ММ-ДД.")
        return
    data = await state.get_data()
    from_d, to_d = data["custom_from"], d

    try:
        if datetime.strptime(from_d, "%Y-%m-%d") > datetime.strptime(to_d, "%Y-%m-%d"):
            await message.answer("❌ Дата начала позже даты окончания. Попробуйте ещё раз.", reply_markup=get_cancel_inline())
            await state.set_state(AdminStats.custom_from)
            return
    except ValueError:
        await state.clear()
        await message.answer("❌ Ошибка дат. Начните заново: /start")
        return

    await state.clear()
    loading_msg = await message.answer(f"⏳ Формирую отчёт за период {from_d} — {to_d}...")

    await send_stats(message, from_d, to_d)

    try:
        await loading_msg.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение-заглушку: %s", e)

    info = await get_user_info(message.from_user.id)
    await message.answer(
        "👋 Выберите действие:",
        reply_markup=get_start_menu_inline(info["role"] if info else "admin"),
    )

# ====================
# ИМПОРТ СТУДЕНТОВ
# ====================

@router.callback_query(F.data == CB_ADMIN_IMPORT)
async def cb_admin_import(callback: CallbackQuery, state: FSMContext):
    if not await _require_admin(callback, callback.from_user.id):
        return
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "📥 Отправьте файл со списком студентов (.xlsx, .xls или .csv).\n\n"
        "📋 Формат «Приказ»: заголовок группы (например, 28-143), затем строки [номер, Фамилия, Имя, Отчество].\n"
        "📋 Плоский формат: колонки ФИО, Группа, (опционально) Пароль.\n\n"
        "⚠️ Студенты, которых нет в новом списке, будут перенесены в архив "
        "(вернутся из него, если снова появятся в списке).\n"
        "🔑 Для новых студентов бот сгенерирует пароли и пришлёт их Excel-файлом.",
        reply_markup=get_cancel_inline(),
    )
    await state.set_state(AdminImport.waiting_file)
    await callback.answer()

@router.message(AdminImport.waiting_file, F.document)
async def process_import_file(message: Message, state: FSMContext):
    doc = message.document
    # Проверка размера (5 МБ)
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await message.answer("❌ Файл слишком большой (макс. 5 МБ). Отправьте файл меньшего размера.")
        return

    # Проверка расширения
    filename = doc.file_name or ""
    ext = Path(filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        await message.answer("❌ Неподдерживаемый формат. Используйте .xlsx, .xls или .csv.")
        return

    # Создаем сообщение-заглушку
    loading_msg = await message.answer("⏳ Скачиваю и обрабатываю файл...")

    # Скачиваем файл
    try:
        file_bytes = await message.bot.download(doc.file_id)
    except Exception as e:
        # 🔒 БЕЗОПАСНОЕ УДАЛЕНИЕ через message.bot
        await message.bot.delete_message(chat_id=message.chat.id, message_id=loading_msg.message_id)
        await message.answer(f"❌ Не удалось скачать файл: {e}")
        return

    # Сохраняем во временный файл
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes.read())
            tmp_path = Path(tmp.name)

        result = await import_students_from_file(tmp_path)
    except Exception as e:
        # 🔒 БЕЗОПАСНОЕ УДАЛЕНИЕ через message.bot
        await message.bot.delete_message(chat_id=message.chat.id, message_id=loading_msg.message_id)
        await message.answer(f"❌ Ошибка при импорте: {e}")
        return
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)

    # 🔒 БЕЗОПАСНОЕ УДАЛЕНИЕ заглушки при успехе
    try:
        await message.bot.delete_message(chat_id=message.chat.id, message_id=loading_msg.message_id)
    except Exception as e:
        logger.warning("Не удалось удалить сообщение-заглушку: %s", e)

    # Формируем отчёт
    report = (
        f"✅ Импорт завершён!\n\n"
        f"📥 Добавлено новых: {result['added']}\n"
        f"♻️ Возвращено из архива: {result['restored'] + result['reactivated']}\n"
        f"⏸️ Уже активных: {result['kept']}\n"
        f"🗄 Перенесено в архив: {result['archived']}\n"
        f"⚠️ Отбраковано строк: {result['skipped_parse']}\n\n"
        f"📊 Итого:\n"
        f"  Активных: {result['total_active']}\n"
        f"  В архиве: {result['total_archived']}"
    )
    await message.answer(report)

    # Если есть новые пароли — присылаем Excel
    if result["new_passwords"]:
        buffer = build_passwords_excel(result["new_passwords"])
        file = BufferedInputFile(buffer.read(), filename=f"passwords_{len(result['new_passwords'])}_new.xlsx")
        await message.answer_document(
            document=file,
            caption=f"🔑 Пароли для {len(result['new_passwords'])} новых студентов.\nРаспечатайте и раздайте."
        )
    else:
        await message.answer("ℹ️ Новых студентов нет — пароли не генерировались.")

    await state.clear()
    info = await get_user_info(message.from_user.id)
    await message.answer(
        "👋 Выберите действие:",
        reply_markup=get_start_menu_inline(info["role"] if info else "admin"),
    )

@router.message(AdminImport.waiting_file, F.text)
async def import_cancel_by_text(message: Message, state: FSMContext):
    await state.clear()
    info = await get_user_info(message.from_user.id)
    await message.answer(
        "Импорт отменён.",
        reply_markup=get_start_menu_inline(info["role"] if info else "admin"),
    )

# ====================
# ОТМЕНА
# ====================

@router.callback_query(
    StateFilter(AdminStats.choosing_period, AdminStats.custom_from, AdminStats.custom_to, AdminImport.waiting_file),
    F.data == CB_CANCEL,
)
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    info = await get_user_info(callback.from_user.id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "Действие отменено.",
        reply_markup=get_start_menu_inline(info["role"] if info else "admin"),
    )
    await callback.answer()