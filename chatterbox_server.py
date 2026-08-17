"""Warm Chatterbox-TTS *turbo* server: loads the model once, synthesizes on demand.

Runs in the isolated `venv-chatterbox` (chatterbox-tts pins numpy<2 etc.). The
bash client (qsay.sh -> pipe.sh) POSTs straight to this port.

Uses the distilled turbo model (ChatterboxTurboTTS): a 2-step meanflow decoder
and no CFG, so synthesis runs faster than realtime (~2x the standard model).
Trade-offs vs the standard model:
  * `exaggeration` and `cfg` are IGNORED by turbo -- the query params are still
    accepted but have no effect. `temperature` and `repetition_penalty` DO
    apply and are honored.
  * the reference clip must be > 5s, so voices with a shorter clip (steve, tom,
    q, q2) don't work here; use ship/doctor/jarvis/etc.

Reference-audio conditionals (the expensive part of cloning a voice -- loading
+ resampling + embedding the reference clip) are cached per voice in
COND_CACHE, so repeat requests for the same voice skip straight to inference
instead of re-embedding the reference every time.

POST /say     body = text to speak (raw UTF-8)
              optional query: ?voice=ship&temperature=0.8&repetition_penalty=1.2&out=/path/to/reply.wav
GET  /health  -> "ok" once the model is loaded
GET  /voices  -> newline-separated discovered voice names
GET  /chimes  -> newline-separated discovered chime names
"""

import os
import tempfile
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

warnings.filterwarnings("ignore")

HOST, PORT = os.environ.get("CBX_HOST", "0.0.0.0"), 8766  # 0.0.0.0 = reachable on LAN

# Optional shared-secret auth. Unset (default) = no auth, behavior unchanged.
# Set on both server and client (pipe.sh sends it as a header/query param) to
# require it -- see README. Checked against X-Auth-Token header or ?token=.
CBX_TOKEN = os.environ.get("CBX_TOKEN", "")

# Chatterbox output is very quiet (raw peak ~0.11). Peak-normalize each clip up
# to near full scale so the voice is actually loud. Cap the gain so a near-silent
# clip can't blow up into amplified noise.
TARGET_PEAK = 0.95
MAX_GAIN = 20.0

# Voices are discovered from the voices/ folder by voicelib -- drop an audio
# file, it's a voice. Chatterbox clones from the bare audio, so no transcript
# sidecar is needed.
import httputil
import voicelib

BASE = voicelib.BASE
SCRATCH = os.path.join(BASE, "scratch")
os.makedirs(SCRATCH, exist_ok=True)
# Default voice must have a >5s reference clip (turbo requirement); ship is 7s.
DEFAULT_VOICE = "ship"
DEFAULT_OUT = os.path.join(SCRATCH, "reply.wav")

print("[cbx] loading Chatterbox turbo model...", flush=True)
_t = time.time()
import numpy as np
import soundfile as sf
import torch
from chatterbox.tts_turbo import ChatterboxTurboTTS  # not exported at package top level

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
# The turbo model loads in fp32. On Ampere (this box is an RTX 3070, cap 8.6) the
# autoregressive token decode is matmul-heavy and was leaving the tensor cores
# idle. TF32 runs those matmuls on the tensor cores at ~fp16 speed with fp32-ish
# range (negligible quality change for TTS) -- that's the real win here, and it's
# quality-safe and reversible.
#
# cudnn.benchmark is deliberately LEFT OFF: measured, it HURT this workload. The
# t3 AR decode runs at a new sequence length every step, so benchmark re-autotunes
# cudnn kernels mid-generation -- the stalls that dropped decode from ~40 to ~6
# tok/s. Turning it off took the long-line realtime factor from ~1.1 (stally) to
# ~0.6 (steady, faster than realtime), which is what keeps streamed playback
# gapless. (It only helps fixed-shape conv nets; our decode isn't one.)
if DEVICE == "cuda":
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.backends.cudnn.benchmark = False
TTS = ChatterboxTurboTTS.from_pretrained(device=DEVICE)
SR = TTS.sr
# One shared model instance -> serialize inference. The HTTP layer stays threaded
# (connections queue), but only one /say synthesizes at a time, so overlapping
# requests wait instead of racing and wedging the model.
INFER_LOCK = threading.Lock()
print(f"[cbx] model loaded in {time.time()-_t:.1f}s on {DEVICE}", flush=True)

# voice name -> Conditionals. TTS.generate(audio_prompt_path=...) re-embeds the
# reference clip (load/resample/voice-encoder/s3gen) on every call, even for a
# repeat of the same voice. Caching the resulting TTS.conds per voice and
# swapping it in lets repeat requests skip straight to inference.
COND_CACHE = {}


def synth(text, ref, temperature, out, voice, repetition_penalty=1.2):
    """Synthesize `text` cloning reference audio `ref` to `out` as a 16-bit PCM
    wav (broadly compatible with players/editors; torchaudio would write float).
    Turbo ignores exaggeration/cfg; temperature and repetition_penalty apply.
    `voice` keys COND_CACHE so the reference embedding is reused across calls
    for the same voice instead of recomputed every time."""
    with INFER_LOCK:
        cached = COND_CACHE.get(voice)
        if cached is not None:
            TTS.conds = cached
            wav = TTS.generate(
                text,
                temperature=temperature,
                repetition_penalty=repetition_penalty,
            )
        else:
            wav = TTS.generate(
                text,
                audio_prompt_path=ref,
                temperature=temperature,
                repetition_penalty=repetition_penalty,
            )
            COND_CACHE[voice] = TTS.conds
    # wav is a torch tensor shaped (1, N) on the model device.
    data = wav.squeeze(0).detach().cpu().numpy()
    # Peak-normalize up to TARGET_PEAK so the (very quiet) raw output is loud.
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1e-4:
        data = data * min(TARGET_PEAK / peak, MAX_GAIN)
    data = np.clip(data, -1.0, 1.0)
    sf.write(out, data, SR, subtype="PCM_16")


# Warm up so the first real request is fast too. Also pre-warms COND_CACHE
# for the default voice, so its first real request skips re-embedding too.
try:
    synth("System online.", voicelib.voices()[DEFAULT_VOICE], 0.8, DEFAULT_OUT, DEFAULT_VOICE)
    print("[cbx] warmup complete", flush=True)
except Exception as e:  # pragma: no cover
    print(f"[cbx] warmup failed: {e}", flush=True)


def _safe_out(requested):
    """Confine a client-supplied `out` path to a filename inside SCRATCH.
    Strips any directory component (so ../../ or an absolute path can't
    escape it) and rejects empty/`.`/`..` after that stripping. Caps the
    write primitive to "some filename inside scratch/", auth or not."""
    name = os.path.basename(requested or "")
    if not name or name in (".", ".."):
        name = os.path.basename(DEFAULT_OUT)
    return os.path.join(SCRATCH, name)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authorized(self, q):
        if not CBX_TOKEN:
            return True
        got = self.headers.get("X-Auth-Token") or q.get("token", [""])[0]
        return got == CBX_TOKEN

    def do_GET(self):
        path = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)
        if path == "/health":
            self._send(200, "ok")
        elif not self._authorized(q):
            self._send(401, "unauthorized")
        elif path == "/voices":
            self._send(200, "\n".join(sorted(voicelib.voices())))
        elif path == "/chimes":
            self._send(200, "\n".join(sorted(voicelib.chimes())))
        else:
            self._send(404, "not found")

    def do_POST(self):
        if urlparse(self.path).path != "/say":
            self._send(404, "not found")
            return
        q = parse_qs(urlparse(self.path).query)
        if not self._authorized(q):
            self._send(401, "unauthorized")
            return
        voice = q.get("voice", [DEFAULT_VOICE])[0]
        voices = voicelib.voices()
        if voice not in voices:
            self._send(400, f"unknown voice: {voice}")
            return
        try:
            # exaggeration/cfg are accepted for client compatibility but turbo
            # ignores them; temperature and repetition_penalty are honored.
            temperature = httputil.float_param(q, "temperature", 0.8)
            repetition_penalty = httputil.float_param(q, "repetition_penalty", 1.2)
        except ValueError as e:
            self._send(400, str(e))
            return
        out = _safe_out(q.get("out", [DEFAULT_OUT])[0])
        stream = q.get("stream", ["0"])[0] not in ("0", "", "false", "no")
        n = int(self.headers.get("Content-Length", 0))
        text = self.rfile.read(n).decode("utf-8").strip()
        if not text:
            self._send(400, "empty text")
            return
        t0 = time.time()
        # Remote clients (?stream=1) get the wav bytes in the response body; the
        # server-side file is irrelevant to them, so synth to a temp path and
        # return it. Local clients keep passing ?out=... and read that file.
        if stream:
            tmp = os.path.join(tempfile.gettempdir(), f"cbx_{os.getpid()}_{int(t0*1000)}.wav")
            try:
                synth(text, voices[voice], temperature, tmp, voice, repetition_penalty)
                with open(tmp, "rb") as fh:
                    audio = fh.read()
            except Exception as e:
                self._send(500, f"error: {e}")
                return
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self._send_bytes(200, audio, "audio/wav")
            return
        try:
            synth(text, voices[voice], temperature, out, voice, repetition_penalty)
        except Exception as e:
            self._send(500, f"error: {e}")
            return
        self._send(200, f"{out} ({time.time()-t0:.1f}s)")

    def _send(self, code, body):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_bytes(self, code, data, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"🦆 [cbx] ready on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()
