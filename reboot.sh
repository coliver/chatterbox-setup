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

log "config: port=$port venv=$venv script=$script timeout=${timeout}s"

# Kill the current listener(s) on the port, if any. No listener is the normal
# first-boot case, not an error.
pids="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
if [ -z "$pids" ]; then
  log "no existing listener on port $port"
else
  log "found listener(s) on port $port: $pids"
  for pid in $pids; do
    if kill "$pid" 2>/dev/null; then log "killed old server $pid"; else log "failed to kill $pid (already gone?)"; fi
  done
fi

# Relaunch detached, logging to logs/<stem>.out.log / .err.log. nohup keeps it
# alive after this shell exits; disown drops it from the job table if we have one.
mkdir -p "$dir/logs"
log "launching: $python $script (out -> logs/$stem.out.log, err -> logs/$stem.err.log)"
nohup "$python" "$dir/$script" >"$dir/logs/$stem.out.log" 2>"$dir/logs/$stem.err.log" &
newpid=$!
disown 2>/dev/null || true
log "starting Chatterbox... (pid $newpid)"

# Wait for the model to load and health to go green.
for ((i = 1; i <= timeout; i++)); do
  if [ "$(curl -s -m 2 "http://127.0.0.1:${port}/health" || true)" = "ok" ]; then
    log "ready after ${i}s"
    [ -t 1 ] && "$dir/mascot.sh"
    exit 0
  fi
  if ! kill -0 "$newpid" 2>/dev/null; then
    die "server process $newpid died while waiting for health - check $dir/logs/$stem.err.log"
  fi
  if (( i % 10 == 0 )); then
    log "still waiting for health... (${i}s elapsed, pid $newpid alive)"
  fi
  sleep 1
done
die "timed out waiting for health - check $dir/logs/$stem.err.log"
