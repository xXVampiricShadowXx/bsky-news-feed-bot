#!/usr/bin/env bash
# macOS / Linux launcher: creates the virtual environment, installs dependencies,
# then runs the bot and restarts it if it ever exits (mirrors run_bot.ps1).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv || { echo "Install Python 3.10 or newer, then run this again."; exit 1; }
fi
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example - add your Bluesky handle and app password, then run this again."
    exit 0
fi
.venv/bin/python -m pip install --quiet -r requirements.txt

mkdir -p logs
port="$(grep -E '^APP_PORT=' .env | cut -d= -f2 | tr -d '[:space:]')"
echo "Dashboard: http://127.0.0.1:${port:-5000}   (Ctrl+C to stop)"

delay=5
while true; do
    started=$(date +%s)
    set +e
    .venv/bin/python -u app.py 2>&1 | tee -a "logs/bot-$(date +%F).log"
    code=${PIPESTATUS[0]}
    set -e
    case "$code" in
        10) echo "Another copy of the bot is already running."; exit 0 ;;
        2) echo "Settings problem (see above). Fix .env and start again."; exit 2 ;;
        130|143) exit 0 ;;
    esac
    if [ $(( $(date +%s) - started )) -ge 600 ]; then delay=5; fi
    echo "Bot exited (code $code). Restarting in ${delay}s..."
    sleep "$delay"
    delay=$(( delay * 2 > 300 ? 300 : delay * 2 ))
done
