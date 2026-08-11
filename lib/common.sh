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

# Engine -> base /say URL. Single source of truth for the server ports.
engine_base() {
  case "$1" in
    chatterbox) printf 'http://127.0.0.1:8766/say' ;;
    f5)         printf 'http://127.0.0.1:8765/say' ;;
    *)          die "unknown engine: $1" ;;
  esac
}

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

# Play an audio file through ffplay (ships with ffmpeg). Takes a POSIX path,
# blocks until the clip finishes, and honors DRY_RUN via run().
play() {
  run ffplay -nodisp -autoexit -loglevel quiet "$1"
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
