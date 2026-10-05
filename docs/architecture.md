# Архітектура застосунку

## Компоненти

| Компонент | Технології | Відповідальність |
| --- | --- | --- |
| Web frontend | React + Vite | Інтерфейс фінансових оглядів, проєктів, транзакцій та AI-чату. |
| Web API | FastAPI | API `/api/*`, авторизація через одноразовий Telegram-код, health check і роздавання React-збірки. |
| Telegram-бот | aiogram | Polling Telegram, команди користувача та створення одноразового коду для входу через `/login`. |
| База даних | Neon PostgreSQL | Користувачі, проєкти, транзакції, сесії, ліміти та AI-історія. |
| AI | Gemini | Аналіз транзакцій та AI-помічник фінансів. |
| Production-платформа | Render | Один Docker Web Service із FastAPI, React-збіркою та Telegram-ботом. |

## Потоки запитів

### Web dashboard

```text
Browser
  -> https://ai-finance-remont.pp.ua
  -> Render Web Service
  -> FastAPI
  -> React static files або /api/*
  -> Neon PostgreSQL / Gemini
```

FastAPI віддає зібраний React із `frontend/dist`. Запити фронтенду на `/api/*` обробляє той самий origin, тому окремий CORS-проксі для production не потрібен.

### Авторизація через Telegram

```text
Користувач -> Telegram /login -> Telegram-бот -> Neon (одноразовий код)
Користувач -> Web dashboard -> POST /api/auth/verify -> FastAPI -> Neon (сесія)
```

Сесія браузера зберігається в HttpOnly cookie. У production `SESSION_COOKIE_SECURE=true`, тому cookie надсилається лише через HTTPS.

### AI-функції

```text
Web dashboard -> FastAPI /api/ai/* -> Neon (дані проєкту) -> Gemini -> FastAPI -> Browser
```

`GEMINI_API_KEY` використовується лише на backend і не передається у React-збірку.

## Локальний і production-запуск

### Локально

`docker-compose.yml` запускає два окремі контейнери:

- `api` — FastAPI на `http://localhost:8000`;
- `bot` — Telegram polling.

Vite dev server у `frontend/` проксіює `/api` на локальний FastAPI.

### Production на Render

`Dockerfile.render` створює багатостадійний образ:

1. Node-етап виконує `npm ci` і `npm run build` для React.
2. Python-етап встановлює залежності backend, копіює `app/` і `frontend/dist`.
3. `start-render.sh` запускає FastAPI й Telegram-бота в одному контейнері.

FastAPI слухає `0.0.0.0:$PORT`, який передає Render. Якщо API або бот завершується аварійно, стартовий скрипт завершує контейнер, щоб Render міг перезапустити весь сервіс.

## Health check

Render перевіряє `GET /health`. Endpoint повертає:

```json
{"status":"ok"}
```

## Конфігурація та секрети

Секрети не зберігаються в Git. Для production вони додаються до Render Environment Variables:

- `BOT_TOKEN`;
- `DATABASE_URL`;
- `GEMINI_API_KEY`;
- `SESSION_COOKIE_SECURE=true`.

Деталі розгортання описані в [deploy.md](deploy.md), а DNS і власний домен — у [domain.md](domain.md).
