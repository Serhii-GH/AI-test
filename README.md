# Telegram-бот

Мінімальний Telegram-бот на `aiogram 3.x`.

## Що робить бот

Бот відповідає на команди користувача, показує меню доступних команд у Telegram і працює з токеном із локального файлу `.env`.

## Доступні команди

| Команда | Опис |
| --- | --- |
| `/start` | Вітає користувача та одразу показує можливості бота. |
| `/help` | Показує коротку довідку й перелік команд. |

## Створення `.env`

У корені проєкту створи файл `.env` і додай до нього токен свого бота:

```env
BOT_TOKEN=<токен_від_BotFather>
```

Не додавай `.env` у Git. Він уже виключений у `.gitignore` та `.dockerignore`.

## Встановлення залежностей

Потрібен Python 3.11 або новіший. У PowerShell створи віртуальне середовище й встанови залежності:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Локальний запуск

Після створення `.env` і встановлення залежностей запусти:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

Щоб зупинити бота, натисни `Ctrl+C`.

## Запуск через Docker Compose

Переконайся, що Docker Desktop запущений, а `.env` є в корені проєкту. Далі виконай:

```powershell
docker compose up --build
```

Для запуску у фоновому режимі:

```powershell
docker compose up --build -d
```

Перегляд логів:

```powershell
docker compose logs -f bot
```

Зупинка контейнера:

```powershell
docker compose down
```

## Automatic restart у режимі розробки

Docker Compose монтує локальну папку `app` у контейнер. Залежність `watchdog` стежить за змінами у файлах `app/**/*.py` і перезапускає застосунок командою `python -m app.main`.

Щоб перевірити це:

1. Запусти `docker compose up --build`.
2. В окремому PowerShell відкрий логи: `docker compose logs -f bot`.
3. Зміни й збережи будь-який Python-файл у папці `app`, наприклад текст відповіді бота.
4. У логах має бути видно перезапуск процесу бота.

## Додана невелика функція

Додано команду `/help` і меню команд Telegram із `/start` та `/help`. Відповідь на `/start` тепер одразу пояснює всі доступні можливості.

## Що перевірялося після роботи AI

- Синтаксис `app/main.py` перевірено через `python -m py_compile`.
- Перевірено форматування Git через `git diff --check`.
- Перевірено, що `.env` ігнорується Git та виключений із Docker build context.
- Перевірено конфігурацію Docker Compose: передавання `.env`, монтування `app` і команда watcher.

Automatic restart потрібно перевірити під час запущеного `docker compose up --build` за кроками вище.
