#!/bin/bash
# Low-latency "say": split a string into clauses (on . ! ? and ,) and stream
# them through pipe.sh, which synthesizes+plays the first clause ASAP
# (overlapping the chime) while the rest render in the background.
# Time-to-first-word is just the first clause, so KEEP IT SHORT.
#
# Usage: qsay.sh "text to speak" [voice]
#   voice:  server voice key (default ship; turbo needs a >5s reference clip,
#           so short-clip voices like steve/tom/q/q2 won't work here)
#   knobs via env: EXAG/CFG (Chatterbox); chime via CHIME (see pipe.sh)
#   DRY_RUN=1: passed through to pipe.sh (print commands; synthesize/play nothing)
set -euo pipefail

text="${1:-}"; voice="${2:-ship}"
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
    # Escape regex metacharacters in `from` (pattern side) and `&`/`/`/`\` in
    # `to` (replacement side) before interpolating -- an unescaped entry
    # (e.g. "c++") would otherwise break or misbehave as a sed pattern.
    from_esc="$(printf '%s' "$from" | sed -E 's/[.[\*^$/\\]/\\&/g')"
    to_esc="$(printf '%s' "$to" | sed -E 's/[&/\\]/\\&/g')"
    text="$(printf '%s' "$text" | sed -E "s/\\b${from_esc}\\b/${to_esc}/gI")"
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
  #
  # Exception: a comma list ("bob, jack, and sally") would otherwise
  # fragment into near-empty clips ("jack," / "and sally" as their own
  # files/clips). Protect commas inside a run of 2+ short (<=3 word) items
  # ending in "and/or/nor <item>" with a placeholder (\x01) before
  # splitting, then restore them after, so the whole list stays one line.
  # Item words exclude and/or/nor themselves so the FIRST such conjunction
  # is always the list's terminator, not swallowed as another item (else
  # "bob, jack, and sally, and they agreed" over-merges into one line). A
  # lone "X, and Y" (independent clauses, not a list) needs 2+ items to
  # trigger, so it still splits as before.
  #
  # Also protect the comma next to a direct address / honorific so it doesn't
  # peel off into a near-empty clip: "You're right, sir." must stay one line,
  # not "You're right," + "sir.". Both positions count -- trailing (", sir")
  # and leading ("Sir, ..."). Same \x01 placeholder, restored at the end.
  vocatives='sir|sirs|madam|madame|ma.?am|milord|milady|mistress|gentlemen|number one'
  # Sequence lead-ins ("First, ..." / "Second, ...") get the same treatment, but
  # leading-only and anchored to sentence start, so a mid-sentence "do this
  # first, then that" still splits normally.
  leadins='first|second|third|fourth|fifth|finally|lastly'
  printf '%s' "$text" \
    | perl -pe 's/((?:(?:(?!(?:and|or|nor)\b)[\w-]+)(?:\s+(?!(?:and|or|nor)\b)[\w-]+){0,2},\s+){2,})(and|or|nor)\s+((?:(?!(?:and|or|nor)\b)[\w-]+)(?:\s+(?!(?:and|or|nor)\b)[\w-]+){0,2})/my ($i,$c2,$c3)=($1,$2,$3); $i=~s{,\s+}{\x01}g; "$i$c2 $c3"/gie' \
    | perl -pe "s/,\\s+(?=(?:$vocatives)\\b)/\\x01/gi; s/\\b((?:$vocatives)),\\s+/\$1\\x01/gi; s/(^|[.!?]\\s+)((?:$leadins)),\\s+/\$1\$2\\x01/gi" \
    | sed -E 's/([.!?,]+)[[:space:]]+/\1\n/g' \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
    | sed $'s/\x01/, /g' \
    | grep -v '^$' > "$tmp" || true   # grep exits 1 if every line blank; fine here
fi

# Scratch prefix (isolates concurrent runs; override so e.g. a Stop hook speaking
# in the background can't stomp a manual ./qsay.sh's in-flight clips).
# Invoke via `bash` so this doesn't depend on pipe.sh's exec bit (which git
# checkouts on this WSL setup have repeatedly stripped).
bash "$dir/pipe.sh" "${QSAY_PREFIX:-qsay}" "$tmp" "$voice"
