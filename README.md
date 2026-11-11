# meal-bot

Бот заказа питания (MAX) на Python: роутеры, SQLite, импорт студентов из Excel, QR-метки, уведомления.

## Структура
- `run.py` — точка входа
- `bot/` — маршруты (user, staff, admin), клавиатуры, FSM
- `core/` — БД, сервисы, Excel-импорт, QR, уведомления
- `config.py` — настройки из переменных окружения
- `data/init_db.py` — инициализация схемы БД

## Установка
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполните MAX_BOT_TOKEN
```

## Запуск
```bash
python data/init_db.py   # создать БД
python run.py
```

> `.env` не коммитится (см. `.gitignore`). Реальные токены не должны попадать в репозиторий.
