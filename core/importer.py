"""Импорт студентов из Excel/CSV. Переиспользуемая функция для бота и CLI."""
import re
from pathlib import Path
import pandas as pd
import aiosqlite
from config import DB_PATH
from core.services import generate_unique_code, normalize

# Группа: цифры-тире-цифры[буквы] (28-143, 15-224а)
GROUP_RE = re.compile(r"^\d{1,4}-\d{1,4}[a-zа-яё]{0,2}$", re.IGNORECASE)
# Чистое число — нумерация строк (1, 12, 1.0)
NUMBER_RE = re.compile(r"^\d+(?:\.0+)?$")


def _normalize_password(text) -> str:
    if pd.isna(text):
        return ""
    return str(text).strip().upper()


def _cell_to_str(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _join_fio(parts) -> str:
    return " ".join(str(p).strip() for p in parts if str(p).strip())


def _is_valid_fio(name: str) -> bool:
    return bool(name) and not any(ch.isdigit() for ch in name)


def _parse_blocks(df: pd.DataFrame, default_group: str = ""):
    results = []
    skipped = 0
    current_group = default_group
    for _, row in df.iterrows():
        cells = [c for c in row.tolist() if pd.notna(c) and str(c).strip() != ""]
        if not cells:
            continue
        strs = [_cell_to_str(c) for c in cells]
        group_cells = [s for s in strs if GROUP_RE.match(s)]
        if group_cells and len(strs) <= 2:
            current_group = group_cells[0]
            continue
        if not current_group:
            continue
        name_parts = [s for s in strs if not NUMBER_RE.match(s)]
        if len(name_parts) < 2:
            skipped += 1
            continue
        name = _join_fio(name_parts)
        if not _is_valid_fio(name):
            skipped += 1
            continue
        results.append((name, current_group))
    return results, skipped


def _parse_flat(df: pd.DataFrame):
    norm_cols = {normalize(str(c)): c for c in df.columns}
    name_col = group_col = pass_col = None
    for key in ("фио", "фио студента", "имя", "name", "fullname"):
        if key in norm_cols:
            name_col = norm_cols[key]; break
    for key in ("группа", "group", "группа студента"):
        if key in norm_cols:
            group_col = norm_cols[key]; break
    for key in ("пароль", "password", "код"):
        if key in norm_cols:
            pass_col = norm_cols[key]; break
    if not name_col or not group_col:
        return [], {}, 0
    pairs, passwords = [], {}
    skipped = 0
    for _, row in df.iterrows():
        name = normalize(row.get(name_col))
        group = normalize(row.get(group_col))
        if not _is_valid_fio(name) or not GROUP_RE.match(group or ""):
            skipped += 1
            continue
        pairs.append((name, group))
        if pass_col is not None:
            pwd = _normalize_password(row.get(pass_col))
            if pwd:
                passwords[(name, group)] = pwd
    return pairs, passwords, skipped


def _read_students(file_path: Path):
    ext = file_path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        sheets = pd.read_excel(file_path, sheet_name=None, header=None)
    elif ext == ".csv":
        sheets = {"csv": pd.read_csv(file_path, encoding="utf-8-sig", sep=None, engine="python", header=None)}
    else:
        raise ValueError(f"Неподдерживаемый формат: {ext}. Используйте .xlsx, .xls или .csv")
    pairs, passwords = [], {}
    skipped = 0
    for sheet_name, df in sheets.items():
        if df.empty:
            continue
        first = " ".join(normalize(c) for c in df.iloc[0].tolist() if pd.notna(c))
        if ("фио" in first or "фамилия" in first) and "групп" in first:
            df_flat = df.copy()
            df_flat.columns = [str(c).strip() for c in df_flat.iloc[0]]
            p, pw, sk = _parse_flat(df_flat.iloc[1:])
            pairs += p
            passwords.update(pw)
            skipped += sk
        else:
            sheet_group = str(sheet_name).strip() if GROUP_RE.match(str(sheet_name).strip()) else ""
            p, sk = _parse_blocks(df, sheet_group)
            pairs += p
            skipped += sk
    return pairs, passwords, skipped


async def import_students_from_file(file_path: Path) -> dict:
    """
    Импорт студентов из файла. Возвращает dict со статистикой и новыми паролями.
    """
    raw_pairs, file_passwords, skipped = _read_students(file_path)
    seen, students = set(), []
    for name, group in raw_pairs:
        key = (normalize(name), normalize(group))
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            students.append(key)

    if not students:
        raise ValueError("В файле не распознано ни одного студента. Проверьте формат.")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT password FROM allowed_students")
        used_passwords = {r["password"] for r in await cur.fetchall() if r["password"]}

        def gen_password():
            while True:
                pwd = generate_unique_code(6)
                if pwd not in used_passwords:
                    used_passwords.add(pwd)
                    return pwd

        added = reactivated = kept = 0
        new_passwords = []  # (full_name, group_number, password) — для выгрузки

        for name, group in students:
            cur = await db.execute(
                "SELECT id, password, is_active FROM allowed_students "
                "WHERE full_name = ? AND group_number = ?",
                (name, group),
            )
            row = await cur.fetchone()
            file_pwd = file_passwords.get((name, group), "")
            if row is None:
                pwd = file_pwd or gen_password()
                used_passwords.add(pwd)
                await db.execute(
                    "INSERT INTO allowed_students (full_name, group_number, password, is_active) "
                    "VALUES (?, ?, ?, 1)",
                    (name, group, pwd),
                )
                added += 1
                new_passwords.append((name, group, pwd))
            else:
                if file_pwd and file_pwd != row["password"]:
                    await db.execute(
                        "UPDATE allowed_students SET password = ? WHERE id = ?",
                        (file_pwd, row["id"]),
                    )
                if not row["is_active"]:
                    await db.execute(
                        "UPDATE allowed_students SET is_active = 1 WHERE id = ?",
                        (row["id"],),
                    )
                    reactivated += 1
                else:
                    kept += 1

        # Деактивация
        cur = await db.execute(
            "SELECT id, full_name, group_number FROM allowed_students WHERE is_active = 1"
        )
        deactivated = 0
        for r in await cur.fetchall():
            if (r["full_name"], r["group_number"]) not in seen:
                await db.execute(
                    "UPDATE allowed_students SET is_active = 0 WHERE id = ?", (r["id"],)
                )
                deactivated += 1

        # Синхронизация users (только role='student')
        cur = await db.execute(
            "SELECT user_id, full_name, group_number, is_active FROM users "
            "WHERE is_verified = 1 AND role = 'student'"
        )
        users_off = users_on = 0
        for r in await cur.fetchall():
            should = 1 if (r["full_name"], r["group_number"]) in seen else 0
            if should != r["is_active"]:
                await db.execute(
                    "UPDATE users SET is_active = ? WHERE user_id = ?", (should, r["user_id"])
                )
                if should:
                    users_on += 1
                else:
                    users_off += 1

        await db.commit()

        cur = await db.execute("SELECT COUNT(*) AS c FROM allowed_students WHERE is_active = 1")
        total_active = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) AS c FROM allowed_students WHERE is_active = 0")
        total_inactive = (await cur.fetchone())["c"]

    return {
        "added": added,
        "reactivated": reactivated,
        "kept": kept,
        "deactivated": deactivated,
        "users_off": users_off,
        "users_on": users_on,
        "total_active": total_active,
        "total_inactive": total_inactive,
        "new_passwords": new_passwords,
        "skipped_parse": skipped,
    }