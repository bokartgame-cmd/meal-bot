"""Генерация Excel-отчётов по статистике выдач"""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side


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
    ws.merge_cells("A1:C1")
    ws["A1"] = f"Отчёт о выдачах питания за период: с {from_date} по {to_date}"
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="left")

    # Пустая строка
    row_num = 3

    # Заголовки таблицы
    headers = ["№", "ФИО студента", "Группа", "Обедов получено"]
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=row_num, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
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

    # Ширины колонок
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 20

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