"""Генерация Excel-отчётов по статистике выдач"""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side


def _set_text(cell, value):
    """Записать пользовательский текст в ячейку строго как текст.
    Текст, начинающийся с = + - @, Excel иначе принял бы за формулу (студент мог написать
    «=HYPERLINK(...)»), а управляющие символы openpyxl не принимает вовсе."""
    cell.value = ILLEGAL_CHARACTERS_RE.sub("", str(value))
    cell.data_type = "s"


def _add_feedback_sheet(wb: Workbook, from_date: str, to_date: str, feedback: list):
    """Лист «Жалобы и предложения»: все обращения студентов за период статистики"""
    ws = wb.create_sheet("Жалобы и предложения")
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells("A1:G1")
    ws["A1"] = f"Жалобы и предложения студентов за период: с {from_date} по {to_date}"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)

    headers = ["№", "Дата и время", "Тип", "ФИО студента", "Группа", "Текст обращения", "Статус"]
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = border

    kinds = {"complaint": "Жалоба", "suggestion": "Предложение"}
    row_num = 4
    for r in feedback:
        values = [
            r["id"],
            str(r["created_at"])[:16],
            kinds.get(r["kind"], r["kind"]),
            str(r["full_name"]).title(),
            str(r["group_number"]).upper(),
            r["text"],
            "Прочитано" if r["is_read"] else "Новое",
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row_num, column=col)
            if isinstance(value, int):
                cell.value = value
            else:
                _set_text(cell, value)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=(col == 6))
        row_num += 1
    if not feedback:
        ws.cell(row=row_num, column=1, value="(нет обращений за период)")

    complaints = sum(1 for r in feedback if r["kind"] == "complaint")
    suggestions = sum(1 for r in feedback if r["kind"] == "suggestion")
    row_num += 1
    for label, value in (("Всего обращений:", len(feedback)), ("Жалоб:", complaints), ("Предложений:", suggestions)):
        ws.cell(row=row_num, column=1, value=label).font = Font(name="Arial", size=11, bold=True)
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=3)
        ws.cell(row=row_num, column=4, value=value).font = Font(name="Arial", size=11, bold=True)
        row_num += 1

    for col, width in zip("ABCDEFG", (6, 18, 14, 34, 14, 80, 12)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"


def build_stats_excel(from_date: str, to_date: str, stats: dict) -> BytesIO:
    """
    Строит Excel-книгу со статистикой.
    stats: результат get_stats_for_period()
    Возвращает BytesIO с готовым .xlsx.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Статистика"

    # Стили
    title_font = Font(name="Arial", size=14, bold=True)
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    summary_font = Font(name="Arial", size=11, bold=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # Заголовок отчёта
    ws.merge_cells("A1:G1")
    ws["A1"] = f"Отчёт о выдачах питания за период: с {from_date} по {to_date}"
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="left")

    # Пустая строка
    row_num = 3

    # Заголовки таблицы
    headers = ["№", "ФИО студента", "Группа", "Обедов получено",
               "Время получения: мин.", "Время получения: среднее", "Время получения: макс."]
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=row_num, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = thin_border
    row_num += 1

    # Данные
    rows = stats["rows"]
    if rows:
        for idx, r in enumerate(rows, start=1):
            ws.cell(row=row_num, column=1, value=idx).border = thin_border
            ws.cell(row=row_num, column=2, value=r["full_name"]).border = thin_border
            ws.cell(row=row_num, column=3, value=r["group_number"]).border = thin_border
            ws.cell(row=row_num, column=4, value=r["cnt"]).border = thin_border
            for col, key in ((5, "t_min"), (6, "t_avg"), (7, "t_max")):
                cell = ws.cell(row=row_num, column=col, value=r.get(key, "—"))
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center")
            row_num += 1
    else:
        ws.cell(row=row_num, column=1, value="(нет выдач за период)")
        row_num += 1

    # Пустая строка перед сводкой
    row_num += 1

    # Сводка
    summary = [
        ("Общее количество выданных обедов за период:", stats["total_issued"]),
        ("Всего студентов в списке:", stats["active_total"]),
        ("Количество студентов, получивших хотя бы 1 обед:", stats["active_got"]),
        ("Количество студентов не получивших ни одного обеда:", stats["active_not_got"]),
    ]
    for label, value in summary:
        cell_label = ws.cell(row=row_num, column=1, value=label)
        cell_label.font = summary_font
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=3)
        ws.cell(row=row_num, column=4, value=value).font = summary_font
        row_num += 1

    # Время получения по ВСЕМ студентам (те же три числа, что и в таблице по каждому)
    time_all = stats.get("time_all") or {"t_min": "—", "t_avg": "—", "t_max": "—"}
    for label, key in (
        ("Время получения (все студенты) — минимальное:", "t_min"),
        ("Время получения (все студенты) — среднее:", "t_avg"),
        ("Время получения (все студенты) — максимальное:", "t_max"),
    ):
        cell_label = ws.cell(row=row_num, column=1, value=label)
        cell_label.font = summary_font
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=3)
        value_cell = ws.cell(row=row_num, column=4, value=time_all[key])
        value_cell.font = summary_font
        value_cell.alignment = Alignment(horizontal="right")
        row_num += 1

    # Оценки получения обеда студентами (1–5), если есть
    ratings = stats.get("ratings")
    if ratings:
        avg = ratings.get("avg")
        for label, value in (
            ("Средняя оценка получения обеда (1–5):", avg if avg is not None else "—"),
            ("Количество оценок:", ratings.get("count", 0)),
        ):
            cell_label = ws.cell(row=row_num, column=1, value=label)
            cell_label.font = summary_font
            ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=3)
            value_cell = ws.cell(row=row_num, column=4, value=value)
            value_cell.font = summary_font
            value_cell.alignment = Alignment(horizontal="right")
            row_num += 1

    # Ширины колонок
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 20
    for col in ("E", "F", "G"):
        ws.column_dimensions[col].width = 16
    ws.row_dimensions[3].height = 32  # заголовки таблицы в две строки

    # Дополнительный лист: жалобы и предложения за период (если данные переданы)
    if stats.get("feedback") is not None:
        _add_feedback_sheet(wb, from_date, to_date, stats["feedback"])

    # Сохраняем в память
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def build_passwords_excel(passwords: list) -> BytesIO:
    """
    Строит Excel-книгу с паролями студентов.
    passwords: список кортежей [(full_name, group_number, password), ...]
    Возвращает BytesIO с готовым .xlsx.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Пароли студентов"

    # Стили
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # 🔧 СОРТИРОВКА ПО ГРУППАМ
    # Сортируем сначала по группе, затем по ФИО
    sorted_passwords = sorted(passwords, key=lambda x: (x[1].lower(), x[0].lower()))

    # Заголовок таблицы
    ws["A1"] = "Список паролей студентов"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)

    # Заголовки колонок
    headers = ["№", "ФИО студента", "Группа", "Пароль"]
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    # Данные
    for idx, (full_name, group, password) in enumerate(sorted_passwords, start=1):
        ws.cell(row=idx + 3, column=1, value=idx).border = thin_border
        ws.cell(row=idx + 3, column=2, value=full_name).border = thin_border
        ws.cell(row=idx + 3, column=3, value=group).border = thin_border
        ws.cell(row=idx + 3, column=4, value=password).border = thin_border

    # Ширины колонок
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 15

    # Сохраняем в память
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer