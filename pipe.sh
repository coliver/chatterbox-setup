#!/bin/bash
# Usage: pipe.sh <prefix> <textfile> [voice]   (one sentence per line)
#   voice:  server voice key (e.g. "steve"); empty = the server's default voice.
# Knobs via env vars:
#   EXAG (0.5), CFG (0.5): Chatterbox exaggeration / cfg_weight.
#   CHIME: chime name in chimes/ (unset -> auto: matches voice, else weird, else none); set empty for none.
#   DRY_RUN=1: print the curl/playback commands; synthesize/play nothing.
#
# Latency: the first line is synthesized before anything plays, so keep it SHORT.
# The chime plays concurrently with that first synth, so it doesn't add latency.
set -euo pipefail

prefix="${1:-}"; file="${2:-}"; voice="${3:-}"
dir="$(cd "$(dirname "$0")" && pwd)"       # this script's folder (POSIX path)
. "$dir/lib/common.sh"

[ -n "$prefix" ] || die "usage: pipe.sh <prefix> <textfile> [voice]"
[ -f "$file" ]   || die "text file not found: $file"

scr="$dir/scratch"; mkdir -p "$scr"        # generated clips live here, not the repo root
vq=""
if [ -n "$voice" ]; then vq="&voice=${voice}"; fi
# Chime: CHIME unset -> "auto" (chime matching the voice, else weird, else none).
# CHIME=name forces one; CHIME="" disables it. (resolve_chime is in lib/common.sh.)
chime="$(resolve_chime "${CHIME:-auto}" "$voice" "$dir/chimes")"

# Remove any leftover clips for this prefix now and on exit (so an interrupt
# doesn't leave scratch behind); also kill the background synth if still running.
synth_pid=""
cleanup() {
  if [ -n "$synth_pid" ]; then kill "$synth_pid" 2>/dev/null || true; fi
  rm -f "$scr/${prefix}_"*.wav 2>/dev/null || true
}
trap cleanup EXIT
rm -f "$scr/${prefix}_"*.wav 2>/dev/null || true

mapfile -t lines < "$file"
n=${#lines[@]}
[ "$n" -gt 0 ] || die "no lines in $file"

# Chatterbox is peak-normalized server side already, so we don't re-amplify.
eq="&exaggeration=${EXAG:-0.5}&cfg=${CFG:-0.5}"

gen() { local i="$1"
  run curl -s -m 300 -X POST "${CBX_SAY_URL}?out=${scr}/${prefix}_${i}.raw.wav${vq}${eq}" --data-binary "${lines[$i]}" >/dev/null
  # In dry-run nothing was written, so skip the publish below.
  [ "${DRY_RUN:-0}" = "1" ] && return 0
  # Publish atomically via mv so the playback loop never sees a half-written clip.
  mv "$scr/${prefix}_${i}.raw.wav" "$scr/${prefix}_${i}.wav"
}

# Start the chime NOW, concurrently with synthesizing the first line,
# so it overlaps the synth instead of delaying speech.
chime_pid=""
if [ -n "$chime" ] && [ -f "$dir/chimes/${chime}.wav" ]; then
  play "$dir/chimes/${chime}.wav" &
  chime_pid=$!
fi

gen 0
# Synthesize the remaining lines in the background while the first ones play.
( for ((i = 1; i < n; i++)); do gen "$i"; done ) &
synth_pid=$!

# Ensure the chime has finished before the first word, so they don't overlap.
if [ -n "$chime_pid" ]; then wait "$chime_pid" 2>/dev/null || true; fi

# Dry-run: no wavs exist to play; just drain the background synth and report.
if [ "${DRY_RUN:-0}" = "1" ]; then
  wait "$synth_pid" 2>/dev/null || true
  echo "dry run ($n clips)"
  exit 0
fi

for ((i = 0; i < n; i++)); do
  # Wait (bounded) for clip i; if the background synth died without producing it,
  # bail instead of hanging forever.
  waited=0
  while [ ! -f "$scr/${prefix}_${i}.wav" ]; do
    sleep 0.1; waited=$((waited + 1))
    if [ "$waited" -gt 3000 ]; then die "timed out waiting for clip $i (synthesis failed?)"; fi
  done
  play "$scr/${prefix}_${i}.wav"
done
echo "done ($n clips)"
