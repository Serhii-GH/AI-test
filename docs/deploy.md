# Production deployment

## Production URL

Production URL: https://ai-finance-0plb.onrender.com

Custom production URL: https://ai-finance-remont.pp.ua

## Render Web Service

| Render setting | Value |
| --- | --- |
| Branch | `main` |
| Runtime | Docker |
| Docker Build Context Directory | `.` |
| Dockerfile Path | `./Dockerfile.render` |
| Docker Command | Leave empty; `Dockerfile.render` starts `start-render.sh`. |
| Health Check Path | `/health` |
| Auto-Deploy | On Commit |

The single Web Service runs FastAPI and the Telegram bot in one Docker container. React is built during the Docker build and served by FastAPI.

## Environment Variables

Set these values in the Render dashboard; do not commit their real values:

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token. |
| `DATABASE_URL` | Neon PostgreSQL connection string. |
| `GEMINI_API_KEY` | Gemini access for AI features. |
| `SESSION_COOKIE_SECURE` | Set to `true` in production. |

## Deploy verification

After a deploy, confirm:

1. Render Deploy Logs show FastAPI and `bot started` / `Start polling`.
2. `GET /health` returns `200` and `{"status":"ok"}`.
3. The technical Render URL and the custom production URL open the dashboard.
4. `/login` in Telegram sends a one-time login code.
5. Database and Gemini-powered features work without secrets appearing in browser code or logs.

Custom-domain DNS, HTTPS verification and Free-plan limitations are documented in [domain.md](domain.md).
