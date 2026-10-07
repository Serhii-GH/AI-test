# Production operations and recovery

This runbook explains how to release and recover the Render production service safely. It does not replace incident communication or required approvals.

## Normal release flow

1. Create a focused `feature/`, `fix/`, or `maintenance/` branch from `main`.
2. Review the final `git diff` and run `python scripts/preflight.py`.
3. Commit only the intended files, push the branch, and open a Pull Request to `main`.
4. Wait for the required GitHub Actions **Preflight** check to pass.
5. Merge the Pull Request. Render deploys the resulting `main` commit automatically.
6. Run a production smoke test:
   - custom domain opens over HTTPS;
   - `GET /health` returns HTTP 200;
   - React frontend loads;
   - the affected API and main user flow work;
   - make one test AI request when the change affects AI;
   - Render logs contain no continuing 500 errors, exceptions, or restart loops.

## Reverting a bad production commit

Keep the `main` history intact. Do **not** use `git reset --hard` or `git push --force` to recover production.

1. Find the commit to undo:

   ```powershell
   git log --oneline
   ```

2. Create a revert commit:

   ```powershell
   git revert <commit_sha>
   ```

3. Run the shared preflight, push the revert branch, and open a Pull Request.
4. After GitHub Actions passes, merge it into `main` and confirm the Render deployment and smoke test.

The resulting history remains clear:

```text
bad commit -> revert commit -> CI -> Render deploy -> stable production
```

## Emergency Render rollback

Use a Render rollback only when production must be restored faster than the normal Git revert flow allows.

1. Roll back the service to the last known-good deployment in the Render dashboard.
2. Confirm recovery with `/health`, the custom domain, the affected user flow, and Render logs.
3. Repair `main` afterwards: either revert the bad commit or merge a focused fix through the normal PR and CI process.

Render rollback changes the running deployment only. If bad code remains in `main`, a later deploy can reintroduce it.

## Database changes and recovery

Application code and Neon PostgreSQL do not roll back together. Deploying an older Docker image does not restore database schema or data.

Before a release that changes the database, document:

- the forward migration and its expected effect;
- backward compatibility with the currently deployed application;
- a backup or recovery point and the owner authorized to restore it;
- a safe rollback path, preferably a forward corrective migration;
- validation queries and the production smoke test.

Never run destructive database operations or restore a production database without explicit approval. If an incident includes both application code and database changes, stabilize the service first, then decide on database recovery separately.
