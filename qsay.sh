#!/bin/bash
# Low-latency "say": split a string into sentences and stream them through
# pipe.sh, which synthesizes+plays the first sentence ASAP (overlapping the
# chime) while the rest render in the background. Time-to-first-word
# is just the first sentence, so KEEP THE FIRST SENTENCE SHORT.
#
# Usage: qsay.sh "text to speak" [voice]
#   voice:  server voice key (default steve)
#   knobs via env: EXAG/CFG (Chatterbox); chime via CHIME (see pipe.sh)
#   DRY_RUN=1: passed through to pipe.sh (print commands; synthesize/play nothing)
set -euo pipefail

text="${1:-}"; voice="${2:-steve}"
dir="$(cd "$(dirname "$0")" && pwd)"       # this script's folder
. "$dir/lib/common.sh"
[ -n "$text" ] || die "usage: qsay.sh \"text\" [voice]"

# One sentence per line: break after . ! ? (keeping the punctuation), trim blanks.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s' "$text" \
  | sed -E 's/([.!?]+)[[:space:]]+/\1\n/g' \
  | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
  | grep -v '^$' > "$tmp" || true   # grep exits 1 if every line was blank; not an error here

# Scratch prefix (isolates concurrent runs; override so e.g. a Stop hook speaking
# in the background can't stomp a manual ./qsay.sh's in-flight clips).
"$dir/pipe.sh" "${QSAY_PREFIX:-qsay}" "$tmp" "$voice"
