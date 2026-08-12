#!/bin/bash
# Plain assertion tests for lib/common.sh (no bats dependency). Run: bash tests/common.test.sh
set -uo pipefail
dir="$(cd "$(dirname "$0")/.." && pwd)"
. "$dir/lib/common.sh"

fail=0
check() { # check <desc> <expected> <actual>
  if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: expected '$2' got '$3'"; fail=1; fi
}

check "CBX_SAY_URL" "http://127.0.0.1:8766/say" "$CBX_SAY_URL"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# --- resolve_chime -------------------------------------------------------
cdir="$tmp/chimes"; mkdir -p "$cdir"
touch "$cdir/steve.wav" "$cdir/weird.wav"
check "resolve_chime auto -> voice match"   "steve" "$(resolve_chime auto steve "$cdir")"
check "resolve_chime auto -> weird"         "weird" "$(resolve_chime auto doctor "$cdir")"
check "resolve_chime explicit passthrough"  "wopr"  "$(resolve_chime wopr steve "$cdir")"
check "resolve_chime empty -> none"         ""      "$(resolve_chime "" steve "$cdir")"
emptydir="$tmp/nochimes"; mkdir -p "$emptydir"
check "resolve_chime auto -> none"          ""      "$(resolve_chime auto steve "$emptydir")"

# run() in dry-run must NOT execute the command (and must return success).
DRY_RUN=1 run touch "$tmp/dry" || true
if [ -e "$tmp/dry" ]; then echo "FAIL - dry-run executed the command"; fail=1; else echo "ok   - dry-run skips execution"; fi

# run() without dry-run executes the command.
run touch "$tmp/real"
if [ -e "$tmp/real" ]; then echo "ok   - run executes when not dry-run"; else echo "FAIL - run did not execute"; fail=1; fi

if [ "$fail" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILURES"; exit 1; fi
