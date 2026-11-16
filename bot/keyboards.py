"""UI-константы и фабрики inline-клавиатур для MAX"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# --- Роли (иерархия: admin > staff > student) ---
ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"
ROLE_STUDENT = "student"

# --- Тексты кнопок ---
STUDENT_BTN_QR = "🎫 Мой QR-код"
STAFF_BTN_CODE = "✍️ Ввести код вручную"
ADMIN_BTN_STATS = "📊 Статистика"
BTN_HELP = "📖 Инструкция"
BTN_CANCEL = "❌ Отмена"
BTN_REG_BACK = "⬅️ Вернуться к предыдущему шагу"
BTN_RESTART_REG = "🔄 Начать заново"
ADMIN_BTN_IMPORT = "📥 Импорт студентов"
ADMIN_BTN_ARCHIVE = "🗄 Архив студентов"
STUDENT_BTN_FEEDBACK = "💬 Жалобы и предложения"
ADMIN_BTN_FEEDBACK = "📬 Обращения студентов"
ADMIN_BTN_RESTART = "🔄 Перезапустить бота"

STUDENT_MEAL_RECEIVED_TEXT = "✅ Успешное получение! Приятного аппетита."
STUDENT_RATE_PROMPT = "Оцените, насколько удобно прошло получение обеда (1 — плохо, 5 — отлично):"
STUDENT_INACTIVE_TEXT = (
    "⚠️ Вы не значитесь в списке получателей питания на текущий момент.\n"
    "Выдача питания через бот для вас приостановлена.\n"
    "Если это ошибка — обратитесь в деканат."
)

# --- Callback data ---
CB_STUDENT_QR = "student_qr"
CB_STAFF_CODE = "staff_code"
CB_HELP = "help"
CB_CANCEL = "cancel"
CB_REG_BACK = "reg_back"
CB_RESTART_REG = "restart_reg"

CB_ADMIN_STATS = "admin_stats"
CB_STATS_WEEK = "stats_week"
CB_STATS_MONTH = "stats_month"
CB_STATS_YEAR = "stats_year"
CB_STATS_CUSTOM = "stats_custom"

CB_ADMIN_IMPORT = "admin_import"

CB_ADMIN_ARCHIVE = "admin_archive"
CB_ARCHIVE_CLEAR_OLD = "archive_clear_old"
CB_ARCHIVE_CLEAR_ALL = "archive_clear_all"
CB_ARCHIVE_CONFIRM_OLD = "archive_confirm_old"
CB_ARCHIVE_CONFIRM_ALL = "archive_confirm_all"
CB_ARCHIVE_CANCEL = "archive_cancel"

CB_FEEDBACK = "feedback"
CB_FEEDBACK_COMPLAINT = "fb_complaint"
CB_FEEDBACK_SUGGESTION = "fb_suggestion"
CB_FEEDBACK_CANCEL = "fb_cancel"
CB_RATE_PREFIX = "rate:"  # rate:<id выдачи>:<оценка 1-5>

CB_ADMIN_FEEDBACK = "admin_feedback"
CB_FEEDBACK_MARK_READ = "fb_mark_read"
CB_FEEDBACK_ADMIN_BACK = "fbadm_back"

CB_ADMIN_RESTART = "admin_restart"
CB_RESTART_CONFIRM = "restart_confirm"  # к данным кнопки добавляется «:метка_времени»
CB_RESTART_CANCEL = "restart_cancel"

# --- Тексты справок ---
STUDENT_HELP_TEXT = (
    "📖 Как это работает\n\n"
    "1. В столовой покажите сотруднику свой QR-код.\n"
    "2. Одна порция выдаётся один раз в учебный день.\n"
    "3. Код обновляется каждый день, вчерашний QR не сработает."
)
STAFF_HELP_TEXT = (
    "📖 Инструкция сотрудника\n\n"
    "1. Отсканируйте QR студента камерой телефона.\n"
    "2. Либо нажмите «✍️ Ввести код вручную» и введите код с карточки.\n"
    "3. Ответы: ✅ — выдано; ⚠️ — уже выдано сегодня; ❌ — код не найден."
)
ADMIN_HELP_TEXT = (
    "📖 Инструкция администратора\n\n"
    "1. Нажмите «📊 Статистика».\n"
    "2. Выберите период: неделя / месяц / год / свой.\n"
    "3. Бот пришлёт Excel-файл с детальной таблицей и сводкой.\n\n"
    "Выдача ролей: /set_role ID роль (роль: student, staff или admin)\n"
    "Например: /set_role 123456789 staff\n\n"
    "Архив студентов: /archive (или кнопка «🗄 Архив студентов»).\n"
    "Студенты, которых нет в загруженном списке, автоматически уходят в архив "
    "и возвращаются из него, когда снова появляются в списке.\n\n"
    "Обращения студентов: кнопка «📬 Обращения студентов» или /feedback. "
    "Новые обращения приходят сюда сразу.\n"
    "Перезапуск бота: кнопка «🔄 Перезапустить бота» (с подтверждением)."
)
STUDENT_FEEDBACK_INTRO = (
    "💬 Жалобы и предложения\n\n"
    "Расскажите, что не так или что можно улучшить, — сообщение получит администратор.\n"
    "Что вы хотите отправить?"
)

def get_start_menu_inline(role: str) -> InlineKeyboardMarkup:
    """Главное меню в зависимости от роли"""
    keyboard = []
    if role == ROLE_ADMIN:
        # Оставляем только функционал управления, инструкцию убрали
        keyboard.append([InlineKeyboardButton(text=ADMIN_BTN_IMPORT, callback_data=CB_ADMIN_IMPORT)])
        keyboard.append([InlineKeyboardButton(text=ADMIN_BTN_STATS, callback_data=CB_ADMIN_STATS)])
        keyboard.append([InlineKeyboardButton(text=ADMIN_BTN_ARCHIVE, callback_data=CB_ADMIN_ARCHIVE)])
        keyboard.append([InlineKeyboardButton(text=ADMIN_BTN_FEEDBACK, callback_data=CB_ADMIN_FEEDBACK)])
        keyboard.append([InlineKeyboardButton(text=ADMIN_BTN_RESTART, callback_data=CB_ADMIN_RESTART)])
    elif role == ROLE_STAFF:
        keyboard.append([InlineKeyboardButton(text=STAFF_BTN_CODE, callback_data=CB_STAFF_CODE)])
        keyboard.append([InlineKeyboardButton(text=BTN_HELP, callback_data=CB_HELP)])
    else:  # student
        keyboard.append([InlineKeyboardButton(text=STUDENT_BTN_QR, callback_data=CB_STUDENT_QR)])
        keyboard.append([InlineKeyboardButton(text=STUDENT_BTN_FEEDBACK, callback_data=CB_FEEDBACK)])
        keyboard.append([InlineKeyboardButton(text=BTN_HELP, callback_data=CB_HELP)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_rating_inline(meal_id: int) -> InlineKeyboardMarkup:
    """Оценка получения обеда: одна строка кнопок 1–5"""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{n}⭐", callback_data=f"{CB_RATE_PREFIX}{meal_id}:{n}")
        for n in range(1, 6)
    ]])


def get_after_rating_inline() -> InlineKeyboardMarkup:
    """После оценки: кнопка жалоб и предложений"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=STUDENT_BTN_FEEDBACK, callback_data=CB_FEEDBACK)],
    ])


def get_feedback_kind_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Жалоба", callback_data=CB_FEEDBACK_COMPLAINT)],
        [InlineKeyboardButton(text="💡 Предложение", callback_data=CB_FEEDBACK_SUGGESTION)],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_FEEDBACK_CANCEL)],
    ])


def get_feedback_cancel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_FEEDBACK_CANCEL)],
    ])


def get_feedback_admin_inline(unread: int) -> InlineKeyboardMarkup:
    keyboard = []
    if unread:
        keyboard.append([InlineKeyboardButton(
            text=f"✅ Отметить прочитанными ({unread})", callback_data=CB_FEEDBACK_MARK_READ)])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_FEEDBACK_ADMIN_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_restart_confirm_inline(token: str) -> InlineKeyboardMarkup:
    """token — данные кнопки подтверждения с меткой времени (core.restart.make_token)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, перезапустить", callback_data=token)],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_RESTART_CANCEL)],
    ])


def get_archive_menu_inline(total: int, old: int, old_days: int) -> InlineKeyboardMarkup:
    """Меню архива: кнопки очистки показываются, только если есть что очищать"""
    keyboard = []
    if old:
        months = max(1, round(old_days / 30))
        keyboard.append([InlineKeyboardButton(
            text=f"🧹 Удалить старше {months} мес. ({old})", callback_data=CB_ARCHIVE_CLEAR_OLD)])
    if total:
        keyboard.append([InlineKeyboardButton(
            text=f"🗑 Очистить весь архив ({total})", callback_data=CB_ARCHIVE_CLEAR_ALL)])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_ARCHIVE_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_archive_confirm_inline(confirm_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить безвозвратно", callback_data=confirm_cb)],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_ARCHIVE_CANCEL)],
    ])


def get_qr_menu_inline() -> InlineKeyboardMarkup:
    """Клавиатура под карточкой студента: 1-я — обновить QR, 2-я — инструкция"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить QR-код", callback_data=CB_STUDENT_QR)],
            [InlineKeyboardButton(text=STUDENT_BTN_FEEDBACK, callback_data=CB_FEEDBACK)],
            [InlineKeyboardButton(text=BTN_HELP, callback_data=CB_HELP)],
        ]
    )


def get_qr_resend_inline() -> InlineKeyboardMarkup:
    """Алиас для обратной совместимости"""
    return get_qr_menu_inline()


def cancel_menu() -> InlineKeyboardMarkup:
    """Inline-кнопка отмены для FSM-регистрации студента"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_CANCEL)]]
    )


def reg_step_menu() -> InlineKeyboardMarkup:
    """Шаги регистрации со 2-го: «Вернуться к предыдущему шагу» и «Отмена»"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_REG_BACK, callback_data=CB_REG_BACK)],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_CANCEL)],
        ]
    )


def get_cancel_inline() -> InlineKeyboardMarkup:
    """Inline-кнопка отмены для ввода кода сотрудником"""
    return cancel_menu()


def get_restart_reg_inline() -> InlineKeyboardMarkup:
    """Кнопка перезапуска регистрации под сообщением об ошибке"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_RESTART_REG, callback_data=CB_RESTART_REG)]]
    )


def get_stats_period_inline() -> InlineKeyboardMarkup:
    """Клавиатура выбора периода статистики"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Неделя", callback_data=CB_STATS_WEEK)],
            [InlineKeyboardButton(text="📅 Месяц", callback_data=CB_STATS_MONTH)],
            [InlineKeyboardButton(text="📅 Год", callback_data=CB_STATS_YEAR)],
            [InlineKeyboardButton(text="⚙️ Свой период", callback_data=CB_STATS_CUSTOM)],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_CANCEL)],
        ]
    )