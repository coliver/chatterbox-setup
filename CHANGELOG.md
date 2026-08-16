# Changelog

Notable changes to this project, newest first. Format is loose (this is a
personal setup, not a published package) — one entry per meaningful commit
or cluster of commits, in plain language.

## Unreleased

- **Security/hardening**: opt-in `CBX_TOKEN` shared-secret auth on the
  server (all endpoints except `/health`); `out=` is now confined to a
  basename under `scratch/` instead of accepting an arbitrary path (was an
  unauthenticated write-to-arbitrary-path via `/say?out=`). `pronounce.txt`
  substitutions are now regex-escaped before use, so an entry with a
  metacharacter can't misbehave. The Stop-hook's `flock` now guards only
  playback, not synthesis, so a queued reply's synth overlaps the previous
  reply's playback instead of waiting idle for it.

## Earlier history

- **Talkback concurrency**: replies queue instead of interrupting each
  other (`flock`-based), replacing the old kill-and-restart behavior;
  synth speed tuned (TF32 on, `cudnn.benchmark` off — benchmark measured to
  *hurt* the AR decode; see `NOTES.md`).
- **Playback**: single persistent PowerShell player process fed via a FIFO,
  removing the ~0.5s cold-start spawn cost per clip; audio routes through
  Windows `Media.SoundPlayer` (native Linux playback paths were scratchy on
  this WSLg host).
- **qsay clause-splitting**: split on `. ! ? ,` for fast time-to-first-word,
  with special-casing to keep comma lists, honorifics/vocatives, and
  sentence-leading enumerators ("First, ...") from fragmenting into
  near-empty clips.
- **Voices/model**: switched to the Chatterbox *turbo* model (faster,
  2-step decode); per-voice reference-conditional caching; `SPEED`/`TEMP`
  knobs; F5 engine removed, Chatterbox-only.
- **Pronunciation**: `pronounce.txt` whole-word respelling for words the
  model mispronounces (`aws` → `A W S`, `gh` → `Github C L I`, etc.).
- **Server**: LAN-reachable bind (`0.0.0.0`) with remote `?stream=1` support
  for clients that just want wav bytes back; `repetition_penalty` exposed.
- **Initial commit**: warm dual-engine TTS servers (Chatterbox + F5) with a
  bash client (`qsay.sh` → `pipe.sh`).

See `git log` for full commit-level detail and `NOTES.md` for engineering
findings (latency, GPU, concurrency gotchas).
