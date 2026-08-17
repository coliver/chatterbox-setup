#!/usr/bin/env python3
"""Stop-hook helper: speak Claude's last assistant message in the ship voice.

Claude Code runs this when the main agent finishes a turn. It receives the hook
JSON on stdin ({"transcript_path": "...", ...}), pulls the most recent assistant
*text* from that JSONL transcript, lightly de-markdowns it, and hands it to
qsay.sh for streaming synthesis+playback.

Playback is launched detached (its own session/process group) so the hook returns
immediately instead of blocking the UI on synthesis. Each utterance runs under an
exclusive flock (PLAYLOCK): a new reply WAITS for the one still speaking to finish
and then plays, so replies queue back-to-back instead of interrupting each other.
When nothing is speaking the lock is free, so a reply with nothing ahead of it
starts with no added wait.

Exits 0 silently on anything unexpected -- a talkback hook must never wedge or
block the turn.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
QSAY = os.path.join(PROJECT, "qsay.sh")
VOICES_DIR = os.path.join(PROJECT, "voices")
DEFAULT_VOICE = os.environ.get("SHIP_HOOK_VOICE", "jarvis-03")  # talkback voice (SPEED 1.2)
LASTFILE = os.path.join(HERE, ".speak.last")  # hash of the message last spoken
# Serialize spoken replies: each utterance runs under an exclusive flock on this
# file, so a new reply WAITS for the one still speaking to finish instead of
# interrupting it (queue, don't supersede). When nothing is speaking the lock is
# free and there's no wait, so the common case is unchanged.
PLAYLOCK = os.path.join(PROJECT, "scratch", "talk.play.lock")

# The Stop hook fires before the just-finished message is flushed to the
# transcript JSONL, so a naive read grabs the PREVIOUS message. We snapshot the
# newest row at start and wait up to POLL_SECONDS for it to change (this turn
# landing), checking every POLL_INTERVAL.
POLL_SECONDS = 3.0
POLL_INTERVAL = 0.15


def last_assistant_text(transcript_path):
    """Return the most recent non-empty assistant text block, or ''."""
    latest = ""
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") != "assistant":
                continue
            content = row.get("message", {}).get("content")
            if isinstance(content, list):
                text = " ".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
            elif isinstance(content, str):
                text = content.strip()
            else:
                text = ""
            if text:
                latest = text
    return latest


SPEAK_RE = re.compile(r"<!--\s*SPEAK:(.*?)-->", re.S)


def spoken_from(raw):
    """Pick what to read aloud. The real spoken gist is the LAST
    `<!--SPEAK: ...-->` marker in the reply -- by convention it sits at the very
    end. Earlier markers are illustrative prose (e.g. a reply that explains the
    talkback mechanism and quotes the marker syntax), so voicing all of them
    would recite the reference material instead of the intended line. With no
    marker, returns empty so the hook stays silent."""
    parts = SPEAK_RE.findall(raw)
    return parts[-1].strip() if parts else ""  # last marker only; none -> silent


CHIME_RE = re.compile(r"<!--\s*CHIME:\s*([A-Za-z0-9_-]+)\s*-->")


def chime_from(raw):
    """Chime name from a `<!--CHIME: name-->` marker, or '' if none. An unknown
    or 'none' name just means no chime plays (pipe.sh skips a missing wav), so
    the assistant picks the chime per reply to match its tone."""
    m = CHIME_RE.search(raw)
    return m.group(1) if m else ""


VOICE_RE = re.compile(r"<!--\s*VOICE:\s*([A-Za-z0-9_-]+)\s*-->")


def voice_from(raw):
    """Voice name from a `<!--VOICE: name-->` marker, validated against the
    voice files actually present in voices/ (name.<ext> for any extension) so
    a marker can only ever select a real, existing voice -- never a path or
    arbitrary string. No match (missing marker or unknown name) -> ''."""
    m = VOICE_RE.search(raw)
    if not m:
        return ""
    name = m.group(1)
    try:
        stems = {os.path.splitext(f)[0] for f in os.listdir(VOICES_DIR)}
    except OSError:
        return ""
    return name if name in stems else ""


def despeakify(text):
    """Strip markdown/emoji noise so the voice reads prose, not syntax."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)      # fenced code blocks
    text = re.sub(r"`([^`]*)`", r"\1", text)                 # inline code -> content
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)   # links/images -> label
    text = re.sub(r"https?://\S+", " ", text)                # bare URLs
    text = re.sub(r"[*_~#>|]", " ", text)                    # emphasis/heading/table marks
    text = re.sub(r"[^\x00-\x7F]", " ", text)                # drop emoji / non-ASCII
    text = re.sub(r"\s+", " ", text)                         # collapse whitespace
    return text.strip()


def read_last_hash():
    try:
        with open(LASTFILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    tpath = payload.get("transcript_path")
    if not tpath or not os.path.isfile(tpath):
        return

    prev_hash = read_last_hash()

    def newest():
        try:
            return despeakify(spoken_from(last_assistant_text(tpath)))
        except OSError:
            return ""

    def h(s):
        return hashlib.sha1(s.encode()).hexdigest() if s else ""

    # The just-finished message flushes to the transcript ~0.5s AFTER this hook
    # fires, so at start the newest row is still the PREVIOUS turn. Snapshot it,
    # then wait for the newest row to CHANGE -- that change is this turn landing.
    start_text = newest()
    start_hash = h(start_text)
    deadline = time.monotonic() + POLL_SECONDS
    text = ""
    while time.monotonic() < deadline:
        cur = newest()
        if cur and h(cur) != start_hash:
            text = cur  # this turn's message just flushed
            break
        time.sleep(POLL_INTERVAL)
    if not text:
        # No new flush observed within the window. Fall back to the snapshot only
        # if it's genuinely something we haven't spoken (e.g. first run, or the
        # turn had already flushed before we looked).
        if start_text and start_hash != prev_hash:
            text = start_text
    if not text:
        return  # nothing new to say

    try:
        with open(LASTFILE, "w") as f:
            f.write(hashlib.sha1(text.encode()).hexdigest())
    except OSError:
        pass

    # Chime chosen per reply via a <!--CHIME: name--> marker (message has flushed
    # by now). Empty -> CHIME unset -> pipe.sh's auto -> none (no matching chime).
    # Default to a short chirp so the voice never starts cold (startling); a
    # <!--CHIME: name--> marker or SHIP_HOOK_CHIME overrides. CHIME="" via marker
    # "none" still disables (pipe.sh treats a missing wav as no chime).
    last_raw = last_assistant_text(tpath)
    chime = chime_from(last_raw) or os.environ.get("SHIP_HOOK_CHIME", "ship-combadge")
    voice = voice_from(last_raw) or DEFAULT_VOICE

    # Distinct scratch prefix so background hook speech never stomps a manual
    # ./qsay.sh (which defaults to the "qsay" prefix).
    # Salt the scratch prefix per invocation (pid + sub-second time) so this run's
    # clips can never collide with a prior run's leftovers. Without this,
    # KEEP_SCRATCH=1 is unsafe: pipe.sh's "wait for clip i" loop only checks that
    # the file EXISTS (pipe.sh:107), so a stale same-named file from an earlier
    # reply satisfies that check instantly and gets played in place of the new
    # (still-synthesizing) clip -- surfaced as a queued reply audibly playing the
    # wrong content. A unique prefix sidesteps the race entirely.
    salt = f"{os.getpid()}-{int(time.time() * 1000) % 100000}"
    env = dict(
        os.environ,
        QSAY_PREFIX=f"hook-{salt}",
        TEMP="0.6",    # turbo honors only temperature
        SPEED="1.2",   # 20% faster delivery (atempo) -- user preference
        # Sentence-streaming (qsay default): short first sentence -> fast first
        # word. Turbo synthesizes faster than realtime, so streaming stays
        # gapless too.
        # KEEP_SCRATCH left unset (default: 0) -- clips are cleaned up per run
        # again. Diagnosis of the runaway-silence bug is done (upstream issue:
        # github.com/resemble-ai/chatterbox#531); the salted-prefix fix above
        # stays regardless, since it also closes a stale-clip playback race.
    )
    if chime:
        env["CHIME"] = chime
    log = open(os.path.join(HERE, "speak.log"), "w")  # noqa: SIM115 (lives with child)
    log.write(f"==== {time.strftime('%H:%M:%S')} launching: {text[:60]!r} voice={voice} chime={chime or '-'}\n")
    log.flush()
    # Launch detached (own session) so this hook returns at once and never blocks
    # the turn. Synthesis starts immediately, unlocked; pipe.sh itself acquires
    # PLAYLOCK only around actual playback, so a queued reply's synth overlaps
    # the previous reply's playback instead of waiting idle for it (see NOTES.md
    # §2). Free lock -> no wait, so a reply with nothing ahead of it plays
    # immediately as before.
    os.makedirs(os.path.dirname(PLAYLOCK), exist_ok=True)
    subprocess.Popen(
        ["bash", QSAY, text, voice],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
        cwd=PROJECT,
        env=env,
    )


if __name__ == "__main__":
    main()
