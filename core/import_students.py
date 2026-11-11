"""CLI-обёртка для импорта студентов. Использование: python import_students.py file.xlsx"""
import asyncio
import sys
from pathlib import Path
from core.importer import import_students_from_file


async def main():
    if len(sys.argv) != 2:
        print("Использование: python import_students.py <файл.xlsx|.xls|.csv>")
        sys.exit(1)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"❌ Файл не найден: {path.resolve()}")
        sys.exit(1)

    print(f"[1] Чтение файла: {path.resolve()}")
    try:
        result = await import_students_from_file(path)
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)

    print(f"[2] Уникальных студентов: {result['added'] + result['reactivated'] + result['kept']} | отбраковано: {result['skipped_parse']}")
    print(f"[3] ✅ Добавлено: {result['added']} | реактивировано: {result['reactivated']} | уже активных: {result['kept']}")
    print(f"[4] ⛔ Деактивировано: {result['deactivated']}")
    print(f"[5] Синхронизация users: отключено {result['users_off']}, включено {result['users_on']}")
    print(f"[6] Итого: активных {result['total_active']}, неактивных {result['total_inactive']}")
    if result["new_passwords"]:
        print(f"[7] 🔑 Сгенерировано паролей для новых студентов: {len(result['new_passwords'])}")


if __name__ == "__main__":
    asyncio.run(main())