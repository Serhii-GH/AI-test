# Repository guide for AI agents

This file defines the safe default workflow for agents working in this repository. A task prompt describes **what** to change; these instructions describe **how** to work safely in this project.

## Architecture

- **Frontend:** React + Vite in `frontend/`.
- **Backend:** FastAPI application in `app/`; the HTTP application is `app.api:app`.
- **Telegram bot:** `app.main`.
- **Database:** Neon PostgreSQL, accessed through `DATABASE_URL`.
- **AI:** Gemini, configured with `GEMINI_API_KEY`.
- **Production:** Render. `render.yaml` is the deployment configuration.
- **Production container:** `Dockerfile.render`. It builds the frontend and copies the result to `frontend/dist`; FastAPI serves that directory as static files.

Read the relevant code and deployment configuration before changing behavior. Keep frontend and API contract changes compatible when both sides are affected.

## Local development

Create a local `.env` from `.env.example` and fill it only on the developer machine or in the platform secret manager.

Run the API from the repository root:

```powershell
uvicorn app.api:app --reload
```

Run the frontend from the repository root:

```powershell
npm run dev --prefix frontend
```

For the Telegram bot, use:

```powershell
python -m app.main
```

## Production rules

- Production uses `Dockerfile.render`; do not change the Dockerfile, `start-render.sh`, or `render.yaml` unless the task explicitly requires a deployment change.
- Render starts `start-render.sh`, which starts the Telegram bot and `uvicorn app.api:app`.
- FastAPI must listen on `0.0.0.0` and use the `PORT` environment variable (with the existing fallback only if necessary for local/container use).
- The production frontend build must remain in `frontend/dist` and be served by FastAPI.
- `GET /health` is Render's health check and must return HTTP 200 when the web service is ready.
- Do not weaken authentication, authorization, cookie security, CORS, or database access controls without an explicit task and an explanation of the impact.

## Secrets and environment variables

Never commit, paste into source code, or expose in logs:

- `.env` files or their real values
- `DATABASE_URL`
- `GEMINI_API_KEY`
- `BOT_TOKEN`
- `ADMIN_PASSWORD`
- session secrets, tokens, credentials, or any other production secret

Commit only `.env.example` with placeholder values. When adding a required environment variable, add a placeholder and documentation without a real value, update `render.yaml` only when appropriate, and ensure it is configured in Render separately.

## Git workflow

- Keep `main` deployable. Do not make task changes directly on it.
- Start each task from an up-to-date `main` in a dedicated branch:
  - `feature/<short-description>` for new functionality
  - `fix/<short-description>` for bug fixes
  - `maintenance/<short-description>` for tooling, docs, CI, or upkeep
- Make focused commits. Do not include unrelated local files or generated output.
- Push the branch and open a Pull Request. Merge only after CI succeeds and review is complete.
- After merging, Render deploys from `main`; run a production smoke test, including `GET /health`.

## Production recovery and rollback

- Never rewrite `main` history to recover production: do not use `git reset --hard` or `git push --force` on `main`.
- To undo a bad production commit, identify it with `git log --oneline`, then create a new revert commit with `git revert <commit_sha>`. Run preflight, push it through the normal PR and CI flow, then let Render deploy the corrected `main`.
- A Render rollback is an emergency measure to restore the last working deployment quickly. It does not fix the Git repository; follow it with a Git revert or a targeted fix before the next deploy.
- Application-code rollback does not roll back Neon PostgreSQL data or schema. For database-affecting changes, document the forward migration, compatibility plan, backup/recovery approach, and rollback plan before deployment. Do not run destructive database commands without explicit approval.
- Follow the detailed incident procedure in `docs/production-operations.md`.

## Required preflight before completing a task

1. Run the shared preflight command: `python scripts/preflight.py`. It is the source of truth for local checks and must also be run by GitHub Actions; do not replace it with a different CI-only set of checks.
2. If it prints `PREFLIGHT FAILED`, fix the reported issue before committing or pushing. Do not claim it passed unless it was actually run.
3. Inspect and show the final `git diff` (use `git diff --check` as well).
4. Explain which files changed and why; call out any production-impacting change.
5. Report the preflight result, including any prerequisite that was unavailable.
6. Before committing or pushing, confirm `git status` contains no secrets, `.env` files, or unrelated changes.

Do not claim a command passed unless it was actually run. If a check needs credentials, external services, or unavailable tooling, say so clearly and provide the exact command for the developer to run.
