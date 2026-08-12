#!/bin/bash
# Reboot the warm Chatterbox TTS server: kill whatever holds its port, relaunch
# detached, then wait until /health returns "ok".
#   reboot.sh
set -euo pipefail

dir="$(cd "$(dirname "$0")" && pwd)"
. "$dir/lib/common.sh"

# Port / venv / server script / log stem / health timeout (seconds). The health
# timeout is generous because the first run downloads the model weights.
port=8766; venv="venv-chatterbox"; script="chatterbox_server.py"; stem="chatterbox"; timeout=180
python="$dir/$venv/bin/python"

# Kill the current listener(s) on the port, if any. No listener is the normal
# first-boot case, not an error.
pids="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
for pid in $pids; do
  if kill "$pid" 2>/dev/null; then log "killed old server $pid"; fi
done

# Relaunch detached, logging to logs/<stem>.out.log / .err.log. nohup keeps it
# alive after this shell exits; disown drops it from the job table if we have one.
mkdir -p "$dir/logs"
nohup "$python" "$dir/$script" >"$dir/logs/$stem.out.log" 2>"$dir/logs/$stem.err.log" &
disown 2>/dev/null || true
log "starting Chatterbox..."

# Wait for the model to load and health to go green.
for ((i = 1; i <= timeout; i++)); do
  if [ "$(curl -s -m 2 "http://127.0.0.1:${port}/health" || true)" = "ok" ]; then
    log "ready after ${i}s"; exit 0
  fi
  sleep 1
done
die "timed out waiting for health - check $dir/logs/$stem.err.log"
