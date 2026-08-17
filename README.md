# Quackerbox 🦆

*("Quick Chatterbox" → Quackerbox. All credit for the actual TTS engine goes
to [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox) — this
repo is just the always-warm server + low-latency client wrapped around it.)*

Always-warm local text-to-speech with a low-latency bash client. The
**Chatterbox turbo** server (`chatterbox_server.py`, port `8766`) loads its model
once and synthesizes on demand, so requests avoid cold-start latency. It clones
from bare reference audio (no transcript needed).

Uses the distilled **turbo** model (`ChatterboxTurboTTS`): a 2-step decoder and
no CFG, so synthesis runs faster than realtime. Two consequences: `exaggeration`
and `cfg` are ignored (only `temperature` applies), and each voice's reference
clip must be **> 5 s** (shorter clips are rejected by turbo). Adjust speaking
rate client-side with `SPEED` (ffmpeg atempo).

## Layout

```
chatterbox_server.py    Chatterbox TTS server (:8766)
voicelib.py             voice/chime discovery (filesystem = registry)
httputil.py             query-param parsing helper
qsay.sh                 streaming "say": split text, synthesize + play per sentence
pipe.sh                 core driver used by qsay.sh (POST, publish, play)
reboot.sh               (re)start the server
lib/common.sh           shared bash helpers (log, run, play, resolve_chime)
tests/                  all tests (Python unittest + shell)
logs/                   server logs, written by reboot.sh (gitignored)
scratch/                generated clips + default output wav (gitignored)
voices/                 drop reference audio here (contents are gitignored)
chimes/                 drop chimes here (contents are gitignored)
```

## Prerequisites

Targets **Linux / WSL2** (the drivers use `lsof` and POSIX paths).

- Python 3.12 with `venv`. Chatterbox needs its own virtualenv `venv-chatterbox/`
  (`chatterbox-tts`, which pins `numpy<2`, etc.).
- [ffmpeg](https://ffmpeg.org/) on `PATH` (used to synthesize/normalize):
  `sudo apt-get install -y ffmpeg`. `lsof` (used by `reboot.sh` to free the port)
  is usually already present.
- **WSL2 with Windows interop** for playback: clips play via `powershell.exe`
  SoundPlayer over the `\\wsl.localhost\...` path (see below).
- A CUDA GPU is used automatically if available, else CPU. On **WSL2**, CUDA works
  through the Windows driver (`/usr/lib/wsl/lib/libcuda.so`) — no in-WSL driver
  install needed.
- **Audio playback**: clips play through the **Windows** audio stack —
  `play()` translates the path with `wslpath -w` and hands it to PowerShell's
  `Media.SoundPlayer`. On this WSLg host every native Linux route (ffplay/SDL,
  `ffmpeg -f pulse`) was scratchy, while the same wav plays cleanly on Windows.
  SoundPlayer only handles PCM wav, which is what the server writes.
  - **If playback fails outright**, make sure Windows interop works
    (`powershell.exe` runs from WSL) and the clip is reachable at its
    `\\wsl.localhost\...` path; if the bridge is wedged, `wsl --shutdown` from
    Windows and reopen.

## Setup

1. Create the Chatterbox venv:
   ```bash
   python3 -m venv venv-chatterbox && venv-chatterbox/bin/pip install -U pip chatterbox-tts
   ```
   The turbo weights (~4 GB, `ResembleAI/chatterbox-turbo`) download automatically
   on first server start; they cache under `~/.cache/huggingface`.
2. Add voices — drop an audio file into `voices/`; its filename (without
   extension) becomes the voice name. Recognized: `.wav .flac .mp3 .ogg .m4a .opus`.
   The reference clip must be **> 5 s** (turbo requirement).
3. Add chimes (optional) — drop a **PCM `.wav`** into `chimes/`; its name becomes
   a chime. A chime named after a voice is auto-selected for that voice.

Discovery runs per request, so newly dropped files work immediately — no restart.

## Run

```bash
./reboot.sh   # start / restart Chatterbox on :8766
```

## Use

```bash
./qsay.sh "Right then. Keep the first sentence short."   # voice ship (default)
./qsay.sh "Text to speak" doctor                         # a different voice
CHIME="" ./qsay.sh "Text to speak" ship                  # no chime (CHIME=name forces one)
SPEED=1.2 ./qsay.sh "Text to speak"                      # 20% faster delivery
DRY_RUN=1 ./qsay.sh "Text to speak"                      # print commands; synthesize/play nothing
```

Knobs via env vars (see `pipe.sh`): `TEMP` (temperature), `SPEED` (atempo rate).
With no `CHIME` set, a chime named after the voice is used, else `weird`, else none.

## HTTP API

`POST /say` — request body is the raw UTF-8 text. Query params:

| Param          | Default            | Notes                                  |
|----------------|--------------------|----------------------------------------|
| `voice`        | `ship`             | must exist in `voices/`, reference > 5 s |
| `out`          | `scratch/reply.wav`| **filename only** — confined to `scratch/`, any directory component is stripped |
| `temperature`  | `0.8`              | number                                 |

`exaggeration`/`cfg` are accepted (client compatibility) but ignored by turbo.
Malformed numeric params return `400` with a message. Other endpoints:
`GET /health` → `ok` (never requires auth), `GET /voices`, `GET /chimes`.

**Auth (opt-in):** the server binds `0.0.0.0` (LAN-reachable) with no auth by
default. Set `CBX_TOKEN=<secret>` in the environment `reboot.sh` starts the
server in, and the same value in the environment `qsay.sh`/the Claude Code
Stop hook run under, to require it — requests must then send it as either the
`X-Auth-Token` header or `?token=` query param, or get `401`. Leave unset for
unchanged (no-auth) behavior.

## Tests

```bash
venv-chatterbox/bin/python -m unittest discover -s tests -t .   # Python, stdlib only
bash tests/common.test.sh                                       # shell helpers
```

## Lint

```bash
venv-chatterbox/bin/python -m ruff check .               # Python (policy in ruff.toml)
bash -n qsay.sh pipe.sh reboot.sh lib/common.sh          # shell syntax check
# Deeper shell lint (optional): apt-get install shellcheck && shellcheck *.sh lib/*.sh
```

`ruff` is a dev-only tool (`venv-chatterbox/bin/pip install ruff`); it is not
a runtime dependency of the server.
