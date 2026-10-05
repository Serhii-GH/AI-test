#!/bin/sh
set -eu

# Render runs one container for this application. Keep the web API and the
# Telegram polling process together, and terminate both if either one stops.
python -m app.main &
bot_pid=$!

uvicorn app.api:app --host 0.0.0.0 --port "${PORT:-10000}" &
api_pid=$!

cleanup() {
    trap - INT TERM EXIT
    kill -TERM "$bot_pid" "$api_pid" 2>/dev/null || true
    wait "$bot_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

while kill -0 "$bot_pid" 2>/dev/null && kill -0 "$api_pid" 2>/dev/null; do
    sleep 1
done

status=0
if ! kill -0 "$bot_pid" 2>/dev/null; then
    wait "$bot_pid" || status=$?
else
    wait "$api_pid" || status=$?
fi

exit "$status"
