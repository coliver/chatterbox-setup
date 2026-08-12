# shellcheck shell=bash
# Shared helpers for the bash driver scripts (pipe.sh, qsay.sh).
# Source AFTER defining $dir (the script's own folder):
#   . "$dir/lib/common.sh"
# Pure bash + the tools already used elsewhere (curl/ffmpeg/ffplay); no
# new dependencies.

# Timestamped log line to stderr, so stdout stays clean for real output.
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# Log an error and exit non-zero.
die() { log "ERROR: $*"; exit 1; }

# Chatterbox /say endpoint. Single source of truth for the server's port.
CBX_SAY_URL='http://127.0.0.1:8766/say'

# Dry-run-aware command runner. With DRY_RUN=1, print the command and skip it
# (returns success); otherwise execute it. Use for side-effecting commands so
# a dry run performs no synthesis/playback.
run() {
  if [ "${DRY_RUN:-0}" = "1" ]; then
    log "DRYRUN would run: $*"
    return 0
  fi
  "$@"
}

# Play an audio file through PulseAudio via ffmpeg's native libpulse output.
# Takes a POSIX path, blocks until the clip finishes, honors DRY_RUN via run().
# We deliberately avoid ffplay here: on WSLg its SDL backend resamples with a
# low-quality resampler into a small callback buffer, which crackles/underruns
# ("scratchy"). `ffmpeg -f pulse` hands the audio to libpulse directly and plays
# cleanly. The trailing arg is the stream name shown in the mixer; the sink is
# the pulse default (override with the PULSE_SINK env var if needed).
# The pulse muxer tears the stream down on exit without draining, so whatever is
# still in its buffer (up to buffer_duration) is dropped -- clipping the last
# word. We bound that buffer to 200ms and pad 0.35s of trailing silence, so the
# dropped tail lands in the silence and real speech always makes it out.
play() {
  run ffmpeg -v error -i "$1" -af "apad=pad_dur=0.35" -f pulse -buffer_duration 200 chatterbox
}

# Resolve which chime to play. "auto" (the default when no chime was specified)
# picks a chime named after the voice if chimes/<voice>.wav exists, else "weird",
# else none. Any other value passes through unchanged.
# Prints the resolved name ("" = no chime).
#   resolve_chime <requested> <voice> <chimes_dir>
resolve_chime() {
  local requested="$1" voice="$2" cdir="$3"
  if [ "$requested" != "auto" ]; then printf '%s' "$requested"; return; fi
  if [ -n "$voice" ] && [ -f "$cdir/$voice.wav" ]; then printf '%s' "$voice"; return; fi
  if [ -f "$cdir/weird.wav" ]; then printf 'weird'; return; fi
  printf ''
}
