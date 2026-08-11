# Warm TTS servers

Two always-warm local text-to-speech servers plus low-latency driver scripts.
Each server loads its model once and synthesizes on demand, so requests avoid
cold-start latency.

- **F5-TTS** — `server.py`, port `8765`. Needs a reference transcript per voice.
- **Chatterbox** — `chatterbox_server.py`, port `8766`. Clones from bare audio
  (no transcript) and adds an `exaggeration` emotion knob. Generally higher
  quality; F5 is faster / lighter.

The F5 server can proxy Chatterbox requests (`?engine=chatterbox`), so you can
hit a single port if you like, but the drivers talk to each engine directly.

## Layout

```
server.py               F5-TTS server (:8765)
chatterbox_server.py    Chatterbox server (:8766, separate venv)
voicelib.py             shared voice/chime discovery (filesystem = registry)
httputil.py             shared query-param parsing helpers
qsay.sh                 streaming "say": split text, synthesize + play per sentence
pipe.sh                 core driver used by qsay.sh (POST, normalize, play)
reboot.sh               (re)start a server: reboot.sh [f5|chatterbox]
lib/common.sh           shared bash helpers (log, run, engine_base, play, resolve_chime)
tests/                  all tests (Python unittest + shell)
logs/                   server logs, written by reboot.sh (gitignored)
scratch/                generated clips + default output wav (gitignored)
voices/                 drop reference audio here (contents are gitignored)
chimes/                 drop chimes here (contents are gitignored)
```

## Prerequisites

- Python 3.12, with **two** virtualenvs (the engines have conflicting pins):
  - `venv/` — install `f5-tts`.
  - `venv-chatterbox/` — install `chatterbox-tts` (pins `numpy<2`, etc.).
- [ffmpeg](https://ffmpeg.org/) **and `ffplay`** on `PATH` (ffplay ships with
  ffmpeg; used for audio processing and playback respectively).
- A CUDA GPU is used automatically if available, else CPU.
- The drivers are bash and assume Git Bash (they use `cygpath`, `netstat`,
  `taskkill`). No PowerShell required.

## Setup

1. Create the two venvs and install each engine into its own:
   ```bash
   py -3.12 -m venv venv            && venv/Scripts/pip install f5-tts
   py -3.12 -m venv venv-chatterbox && venv-chatterbox/Scripts/pip install chatterbox-tts
   ```
2. Add voices — drop an audio file into `voices/`; its filename (without
   extension) becomes the voice name. Recognized: `.wav .flac .mp3 .ogg .m4a .opus`.
   - For **F5**, also add a `<name>.txt` next to it containing the exact words
     spoken in that clip (the reference transcript). Chatterbox ignores it.
3. Add chimes (optional) — drop a **PCM `.wav`** into `chimes/`; its name becomes
   a chime. A chime named after a voice is auto-selected for that voice.

Discovery runs per request, so newly dropped files work immediately — no restart.

## Run

```bash
./reboot.sh              # start / restart F5 on :8765, waits for /health
./reboot.sh chatterbox   # start / restart Chatterbox on :8766
```

## Use

```bash
./qsay.sh "Right then. Keep the first sentence short."   # chatterbox (default), voice steve
./qsay.sh "Text to speak" doctor f5                      # f5 engine
CHIME="" ./qsay.sh "Text to speak" steve                 # no chime (CHIME=name forces one)
DRY_RUN=1 ./qsay.sh "Text to speak"                      # print commands; synthesize/play nothing
```

Engine knobs via env vars (see `pipe.sh`): chatterbox `EXAG`/`CFG`, f5 `NFE`/`GAIN`.
With no `CHIME` set, a chime named after the voice is used, else `weird`, else none.

## HTTP API

`POST /say` — request body is the raw UTF-8 text. Query params:

| Param        | Engine      | Default | Notes                                        |
|--------------|-------------|---------|----------------------------------------------|
| `engine`     | :8765 only  | `f5`    | `f5` or `chatterbox` (proxied to :8766)      |
| `voice`      | both        | server  | must exist in `voices/`                      |
| `out`        | both        | scratch/reply.wav | output wav path                    |
| `chime`      | :8765       | none    | chime name from `chimes/` to prepend         |
| `nfe`        | f5          | `16`    | integer NFE steps                            |
| `speed`      | f5          | `1.0`   | number                                       |
| `exaggeration` | chatterbox | `0.5`  | intensity, 0..1+                             |
| `cfg`        | chatterbox  | `0.5`   | lower (~0.3) steadies pacing when hot        |
| `temperature`| chatterbox  | `0.8`   | number                                       |

Malformed numeric params return `400` with a message. Other endpoints:
`GET /health` → `ok`, `GET /voices`, `GET /chimes` (F5 only).

## Tests

```bash
venv/Scripts/python -m unittest discover -s tests -t .   # Python, stdlib only
bash tests/common.test.sh                                # shell helpers
```

## Lint

```bash
venv/Scripts/python -m ruff check .                      # Python (policy in ruff.toml)
bash -n qsay.sh pipe.sh reboot.sh lib/common.sh          # shell syntax check
# Deeper shell lint (optional): choco install shellcheck && shellcheck *.sh lib/*.sh
```

`ruff` is a dev-only tool (install with `venv/Scripts/pip install ruff`); it is
not a runtime dependency of the servers.
