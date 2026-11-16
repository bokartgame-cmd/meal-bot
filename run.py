"""Точка входа бота"""
import asyncio
import logging
import os
import subprocess
import sys
import time

RESTART_EXIT_CODE = 75  # то же значение, что в core/restart.py

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


async def main():
    from bot.main import main as bot_main
    await bot_main()


def _supervise(cmd=None) -> int:
    """Запускает бота дочерним процессом и перезапускает его, когда он просит об этом (по кнопке
    администратора) завершением с кодом RESTART_EXIT_CODE. Любое другое завершение — выход."""
    cmd = cmd or [sys.executable, os.path.abspath(__file__)]
    env = dict(os.environ, MEAL_BOT_SUPERVISED="1", PYTHONUNBUFFERED="1")
    while True:
        proc = subprocess.Popen(cmd, env=env)
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            # Ctrl+C приходит и дочернему процессу (общая консоль): даём ему завершиться сам
            try:
                code = proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.terminate()
                code = proc.wait()
            print("\nБот остановлен")
            return code
        if code != RESTART_EXIT_CODE:
            return code
        print("🔄 Перезапуск бота...")
        time.sleep(1)


if __name__ == "__main__":
    # Без MEAL_BOT_SUPERVISED запускаемся как супервизор (MEAL_BOT_NO_SUPERVISOR=1 — запуск без него)
    if os.environ.get("MEAL_BOT_SUPERVISED") != "1" and os.environ.get("MEAL_BOT_NO_SUPERVISOR") != "1":
        sys.exit(_supervise())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен")