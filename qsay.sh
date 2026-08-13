#!/bin/bash
# Low-latency "say": split a string into clauses (on . ! ? and ,) and stream
# them through pipe.sh, which synthesizes+plays the first clause ASAP
# (overlapping the chime) while the rest render in the background.
# Time-to-first-word is just the first clause, so KEEP IT SHORT.
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

# Pronunciation fixes: respell words the TTS mispronounces (whole-word, case-
# insensitive) from pronounce.txt. Chatterbox has no phoneme input, so this is
# the only lever. Skips blank/comment lines.
pron="$dir/pronounce.txt"
if [ -f "$pron" ]; then
  while read -r from to _; do
    [ -z "$from" ] && continue
    case "$from" in \#*) continue ;; esac
    text="$(printf '%s' "$text" | sed -E "s/\\b${from}\\b/${to}/gI")"
  done < "$pron"
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if [ "${NOSPLIT:-0}" = "1" ]; then
  # Whole utterance as ONE line -> one clip -> a single synth call. Gapless and
  # lowest total latency for a full reply: synth has a ~2s/call floor, so N
  # sentences cost N*2s streamed (with gaps) but only ~one call joined. Collapse
  # newlines/whitespace so it stays a single line for pipe.sh's mapfile.
  printf '%s' "$text" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//' > "$tmp"
else
  # One clause per line: break after . ! ? or , (keeping punctuation), trim
  # blanks. Splitting on commas too (not just sentence ends) shortens the
  # first chunk sent to synthesis, so speech starts sooner even when the
  # lead sentence runs long. Only commas followed by whitespace split (so
  # "3,000" stays intact).
  printf '%s' "$text" \
    | sed -E 's/([.!?,]+)[[:space:]]+/\1\n/g' \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
    | grep -v '^$' > "$tmp" || true   # grep exits 1 if every line blank; fine here
fi

# Scratch prefix (isolates concurrent runs; override so e.g. a Stop hook speaking
# in the background can't stomp a manual ./qsay.sh's in-flight clips).
# Invoke via `bash` so this doesn't depend on pipe.sh's exec bit (which git
# checkouts on this WSL setup have repeatedly stripped).
bash "$dir/pipe.sh" "${QSAY_PREFIX:-qsay}" "$tmp" "$voice"
