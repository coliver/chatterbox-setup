#!/bin/bash
# Usage: pipe.sh <prefix> <textfile> [voice]   (one sentence per line)
#   voice:  server voice key (e.g. "ship"); empty = the server's default voice.
# Knobs via env vars:
#   TEMP (0.8): Chatterbox temperature (turbo ignores exaggeration/cfg).
#   SPEED (1.0): speak faster/slower via ffmpeg atempo (1.2 = 20% faster). 0.5-2.0.
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
# KEEP_SCRATCH=1 leaves the generated clips in scratch/ for inspection (skips
# both the pre-clean and the exit cleanup); the background synth is still reaped.
synth_pid=""; player_pid=""; player_fifo=""
cleanup() {
  if [ -n "$synth_pid" ]; then kill "$synth_pid" 2>/dev/null || true; fi
  if [ -n "$player_pid" ]; then kill "$player_pid" 2>/dev/null || true; fi
  [ -n "$player_fifo" ] && rm -f "$player_fifo" 2>/dev/null || true
  [ "${KEEP_SCRATCH:-0}" = "1" ] || rm -f "$scr/${prefix}_"*.wav 2>/dev/null || true
}
trap cleanup EXIT
[ "${KEEP_SCRATCH:-0}" = "1" ] || rm -f "$scr/${prefix}_"*.wav 2>/dev/null || true

mapfile -t lines < "$file"
n=${#lines[@]}
[ "$n" -gt 0 ] || die "no lines in $file"

# Chatterbox is peak-normalized server side already, so we don't re-amplify.
# Turbo honors only temperature (exaggeration/cfg are ignored server-side).
eq="&temperature=${TEMP:-0.8}"

gen() { local i="$1"
  local raw="$scr/${prefix}_${i}.raw.wav" final="$scr/${prefix}_${i}.wav"
  local -a authhdr=()
  [ -n "${CBX_TOKEN:-}" ] && authhdr=(-H "X-Auth-Token: ${CBX_TOKEN}")
  run curl -s -m 300 -X POST "${authhdr[@]}" "${CBX_SAY_URL}?out=${raw}${vq}${eq}" --data-binary "${lines[$i]}" >/dev/null
  # In dry-run nothing was written, so skip the publish below.
  [ "${DRY_RUN:-0}" = "1" ] && return 0
  # Optional tempo change (atempo preserves pitch), then publish atomically via mv
  # so the playback loop never sees a half-written clip.
  if [ "${SPEED:-1.0}" != "1.0" ] \
     && ffmpeg -v error -y -i "$raw" -filter:a "atempo=${SPEED}" "$scr/${prefix}_${i}.spd.wav" 2>/dev/null; then
    mv "$scr/${prefix}_${i}.spd.wav" "$final"; rm -f "$raw"
  else
    mv "$raw" "$final"
  fi
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

# Only playback is serialized across concurrent qsay.sh invocations (e.g. two
# queued Stop-hook replies): acquire PLAYLOCK here, right before driving the
# player, so a reply that's still synthesizing above doesn't block a prior
# reply's audio, and a reply that finishes synthesizing first doesn't jump the
# queue -- it waits here for the previous reply's playback to finish, then
# plays. Held fd releases naturally on exit via the existing `trap cleanup
# EXIT` below. (NOTES.md §2: replies queue, they don't interrupt.)
exec 9>"$scr/talk.play.lock"
flock -x 9

# ONE long-lived PowerShell player instead of a fresh spawn per clip. Spawning
# powershell+SoundPlayer costs ~0.5s of cold-start, which — paid at every clip
# boundary — was the audible dead air between sentences. Here that cost is paid
# once: the player loops reading Windows wav paths on stdin and PlaySync's each,
# so consecutive clips play back-to-back. We feed it a path only once the clip
# exists, preserving the stream-as-you-synthesize behavior. (SoundPlayer plays
# PCM wav only, same constraint as play().)
player_fifo="$scr/${prefix}.play.fifo"
rm -f "$player_fifo"; mkfifo "$player_fifo"
powershell.exe -NoProfile -Command \
  'while($l=[Console]::In.ReadLine()){(New-Object Media.SoundPlayer $l).PlaySync()}' \
  < "$player_fifo" &
player_pid=$!
exec 3>"$player_fifo"   # hold the write end open across the loop

for ((i = 0; i < n; i++)); do
  # Wait (bounded) for clip i; if the background synth died without producing it,
  # bail instead of hanging forever.
  waited=0
  while [ ! -f "$scr/${prefix}_${i}.wav" ]; do
    sleep 0.1; waited=$((waited + 1))
    if [ "$waited" -gt 3000 ]; then die "timed out waiting for clip $i (synthesis failed?)"; fi
  done
  printf '%s\n' "$(wslpath -w "$scr/${prefix}_${i}.wav")" >&3
done
exec 3>&-              # EOF -> player exits after finishing the last clip
wait "$player_pid" 2>/dev/null || true
echo "done ($n clips)"
