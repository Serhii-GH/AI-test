# Помилка 1

Команда запуску:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

Текст помилки:

```text
RuntimeError: Змінна середовища BOT_TOKEN не встановлена.
```

Головний рядок traceback:

```text
raise RuntimeError("Змінна середовища BOT_TOKEN не встановлена.")
```

Файл і номер рядка:

`app/main.py:43`

Моя гіпотеза:

Python не отримує токен Telegram-бота зі змінних середовища.

Причина помилки:

Файл `.env` був відсутній, знаходився не в корені репозиторію або не містив змінної з точною назвою `BOT_TOKEN`.

Як я виправив проблему:

Створив `.env` у корені проєкту та додав запис `BOT_TOKEN=<токен від BotFather>`. У `main.py` залишив `load_dotenv()` перед викликом `os.getenv("BOT_TOKEN")`.

Який prompt передав AI:

```text
Допоможи зрозуміти RuntimeError: BOT_TOKEN не встановлена під час запуску aiogram-бота.
```

Як перевірив результат:

Запустив бота повторно. Помилка `RuntimeError` не з'явилась, а в консолі з'явився лог `bot started`.

# Помилка 2

Команда запуску:

```powershell
docker compose up --build
```

Текст помилки:

```text
RuntimeError: Змінна середовища BOT_TOKEN не встановлена.
```

Головний рядок traceback:

```text
raise RuntimeError("Змінна середовища BOT_TOKEN не встановлена.")
```

Файл і номер рядка:

`app/main.py:43`

Моя гіпотеза:

Локально `.env` читається, але Docker Compose не передає його всередину контейнера.

Причина помилки:

У сервісі `bot` не було підключено файл зі змінними середовища, тому `os.getenv("BOT_TOKEN")` у контейнері повертала порожнє значення.

Як я виправив проблему:

У `docker-compose.yml` для сервісу `bot` додав:

```yaml
env_file:
  - .env
```

Також перевірив, що `.env` лежить у корені репозиторію і містить саме `BOT_TOKEN`.

Який prompt передав AI:

```text
Локально Telegram-бот бачить BOT_TOKEN, а в Docker Compose виникає RuntimeError. Як правильно передати .env у контейнер?
```

Як перевірив результат:

Перезібрав і запустив контейнер командою `docker compose up --build`, після чого переглянув логи командою `docker compose logs -f bot`. У логах має бути повідомлення `bot started` без `RuntimeError`.
