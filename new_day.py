"""Сброс состояния «на новый день» — для тестов и отладки.

Что делает (за текущие сутки по BOT_TZ):
  1. сохраняет копию БД в backups/ (если не указан --no-backup);
  2. выдаёт право на обед (одно) всем активным студентам, прошедшим регистрацию;
  3. отменяет все неиспользованные подарки сегодняшнего дня (ссылки перестают работать);
  4. отменяет неподтверждённые запросы на выдачу (кнопка «Подтвердить» у сотрудника перестанет работать);
  5. завершает смену выдачи — сотрудник снова нажимает «Начать выдачу»;
  6. по флагу --wipe-meals удаляет сегодняшние выдачи (и оценки к ним), чтобы день был «чистым».

Запуск (бота можно не останавливать — изменения идут одной транзакцией):
    python new_day.py              # спросит подтверждение
    python new_day.py --yes        # без вопросов
    python new_day.py --wipe-meals # ещё и очистить сегодняшний журнал выдач
    python new_day.py --db путь\\к\\meal_bot.db
Не запускайте на боевой БД без необходимости: журнал выдач очищается только с --wipe-meals.
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytz

import config


def main() -> int:
    parser = argparse.ArgumentParser(description="Сброс состояния на новый день (для тестов)")
    parser.add_argument("--db", type=Path, default=config.DB_PATH, help="путь к БД (по умолчанию из config.py)")
    parser.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждение")
    parser.add_argument("--wipe-meals", action="store_true",
                        help="удалить сегодняшние выдачи (meals_log) и оценки к ним")
    parser.add_argument("--no-backup", action="store_true", help="не делать копию БД перед сбросом")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"❌ БД не найдена: {args.db}")
        return 1

    tz = pytz.timezone(config.BOT_TZ)
    now = datetime.now(tz).replace(tzinfo=None)
    today = now.strftime("%Y-%m-%d")

    print(f"БД: {args.db}")
    print(f"Сутки: {today} ({config.BOT_TZ})")
    print("Будет сделано: права всем активным студентам = 1, подарки и запросы сегодняшнего дня отменены, "
          "смена выдачи сброшена" + (", сегодняшние выдачи удалены" if args.wipe_meals else ""))
    if not args.yes and input("Продолжить? [y/N] ").strip().lower() not in ("y", "yes", "д", "да"):
        print("Отменено.")
        return 0

    if not args.no_backup:
        backup_dir = config.BACKUP_DIR if args.db == config.DB_PATH else args.db.parent
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"meal_bot_before_new_day_{now.strftime('%Y-%m-%d_%H-%M-%S')}.db"
        shutil.copy2(args.db, backup)
        print(f"💾 Копия БД: {backup}")

    conn = sqlite3.connect(args.db, timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            gifts = conn.execute(
                "UPDATE meal_gifts SET status = 'revoked' WHERE day = ? AND status = 'active'", (today,)
            ).rowcount
            requests = conn.execute(
                "UPDATE meal_requests SET status = 'cancelled' WHERE day = ? AND status = 'pending'", (today,)
            ).rowcount
            sessions = conn.execute("DELETE FROM issuance_sessions WHERE day = ?", (today,)).rowcount
            rights = conn.execute(
                """INSERT INTO meal_rights (user_id, day, rights)
                   SELECT user_id, ?, 1 FROM users
                   WHERE role = 'student' AND is_verified = 1 AND is_active = 1
                   ON CONFLICT(user_id, day) DO UPDATE SET rights = 1""",
                (today,),
            ).rowcount
            # права тех, кто перестал быть активным студентом, на сегодня обнуляем
            conn.execute(
                """UPDATE meal_rights SET rights = 0 WHERE day = ? AND user_id NOT IN
                   (SELECT user_id FROM users WHERE role = 'student' AND is_verified = 1 AND is_active = 1)""",
                (today,),
            )
            meals = ratings = 0
            if args.wipe_meals:
                ratings = conn.execute(
                    "DELETE FROM meal_ratings WHERE meal_id IN "
                    "(SELECT id FROM meals_log WHERE date(issued_at) = ?)", (today,)
                ).rowcount
                meals = conn.execute("DELETE FROM meals_log WHERE date(issued_at) = ?", (today,)).rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    print("✅ Готово:")
    print(f"   права выданы студентам: {rights}")
    print(f"   отменено подарков: {gifts}, неподтверждённых запросов: {requests}")
    print(f"   смена выдачи сброшена: {'да' if sessions else 'не была начата'}")
    if args.wipe_meals:
        print(f"   удалено выдач: {meals}, оценок: {ratings}")
    print("Сотруднику нужно снова нажать «Начать выдачу».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
