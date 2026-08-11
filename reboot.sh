#!/bin/bash
# Reboot a warm TTS server: kill whatever holds its port, relaunch detached,
# then wait until /health returns "ok".
#   reboot.sh              # f5 (default)
#   reboot.sh chatterbox
set -euo pipefail

engine="${1:-f5}"
dir="$(cd "$(dirname "$0")" && pwd)"
. "$dir/lib/common.sh"

# Engine -> port / venv / server script / log stem / health timeout (seconds).
case "$engine" in
  f5)         port=8765; venv="venv";            script="server.py";            stem="server";     timeout=120 ;;
  chatterbox) port=8766; venv="venv-chatterbox"; script="chatterbox_server.py"; stem="chatterbox"; timeout=180 ;;
  *) die "unknown engine: $engine (use f5 or chatterbox)" ;;
esac
python="$dir/$venv/Scripts/python.exe"

# Kill the current listener(s) on the port, if any. No listener is the normal
# first-boot case, not an error.
pids="$(netstat -ano | grep -E ":${port}[[:space:]].*LISTENING" | awk '{print $NF}' | sort -u || true)"
for pid in $pids; do
  if taskkill //PID "$pid" //F >/dev/null 2>&1; then log "killed old $engine server $pid"; fi
done

# Relaunch detached, logging to logs/<stem>.out.log / .err.log. nohup keeps it
# alive after this shell exits; disown drops it from the job table if we have one.
mkdir -p "$dir/logs"
nohup "$python" "$dir/$script" >"$dir/logs/$stem.out.log" 2>"$dir/logs/$stem.err.log" &
disown 2>/dev/null || true
log "starting $engine..."

# Wait for the model to load and health to go green (first chatterbox run
# downloads weights, hence its longer timeout).
for ((i = 1; i <= timeout; i++)); do
  if [ "$(curl -s -m 2 "http://127.0.0.1:${port}/health" || true)" = "ok" ]; then
    log "ready after ${i}s"; exit 0
  fi
  sleep 1
done
die "timed out waiting for health - check $dir/logs/$stem.err.log"
