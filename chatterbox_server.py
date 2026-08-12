"""Warm Chatterbox-TTS server: loads the model once, synthesizes on demand.

Runs in the isolated `venv-chatterbox` (chatterbox-tts pins numpy<2 etc.). The
bash client (qsay.sh -> pipe.sh) POSTs straight to this port.

POST /say     body = text to speak (raw UTF-8)
              optional query: ?voice=steve&exaggeration=0.5&cfg=0.5
                              &temperature=0.8&out=/path/to/reply.wav
GET  /health  -> "ok" once the model is loaded
GET  /voices  -> newline-separated discovered voice names
GET  /chimes  -> newline-separated discovered chime names

Chatterbox clones from a bare reference wav (no transcript needed) and adds an
`exaggeration` emotion knob (0..1+, 0.5 neutral). cfg_weight ~0.3 keeps pacing
sane when exaggeration is pushed high.
"""

import os
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

warnings.filterwarnings("ignore")

HOST, PORT = "127.0.0.1", 8766

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
DEFAULT_VOICE = "steve"
DEFAULT_OUT = os.path.join(SCRATCH, "reply.wav")

print("[cbx] loading Chatterbox model...", flush=True)
_t = time.time()
import numpy as np
import soundfile as sf
import torch
from chatterbox.tts import ChatterboxTTS

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TTS = ChatterboxTTS.from_pretrained(device=DEVICE)
SR = TTS.sr
# One shared model instance -> serialize inference. The HTTP layer stays threaded
# (connections queue), but only one /say synthesizes at a time, so overlapping
# requests wait instead of racing and wedging the model.
INFER_LOCK = threading.Lock()
print(f"[cbx] model loaded in {time.time()-_t:.1f}s on {DEVICE}", flush=True)


def synth(text, ref, exaggeration, cfg_weight, temperature, out):
    """Synthesize `text` cloning reference audio `ref` to `out` as a 16-bit PCM
    wav (broadly compatible with players/editors; torchaudio would write float)."""
    with INFER_LOCK:
        wav = TTS.generate(
            text,
            audio_prompt_path=ref,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            temperature=temperature,
        )
    # wav is a torch tensor shaped (1, N) on the model device.
    data = wav.squeeze(0).detach().cpu().numpy()
    # Peak-normalize up to TARGET_PEAK so the (very quiet) raw output is loud.
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1e-4:
        data = data * min(TARGET_PEAK / peak, MAX_GAIN)
    data = np.clip(data, -1.0, 1.0)
    sf.write(out, data, SR, subtype="PCM_16")


# Warm up so the first real request is fast too.
try:
    synth("System online.", voicelib.voices()[DEFAULT_VOICE], 0.5, 0.5, 0.8, DEFAULT_OUT)
    print("[cbx] warmup complete", flush=True)
except Exception as e:  # pragma: no cover
    print(f"[cbx] warmup failed: {e}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, "ok")
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
        voice = q.get("voice", [DEFAULT_VOICE])[0]
        voices = voicelib.voices()
        if voice not in voices:
            self._send(400, f"unknown voice: {voice}")
            return
        try:
            exaggeration = httputil.float_param(q, "exaggeration", 0.5)
            cfg_weight = httputil.float_param(q, "cfg", 0.5)
            temperature = httputil.float_param(q, "temperature", 0.8)
        except ValueError as e:
            self._send(400, str(e))
            return
        out = q.get("out", [DEFAULT_OUT])[0]
        n = int(self.headers.get("Content-Length", 0))
        text = self.rfile.read(n).decode("utf-8").strip()
        if not text:
            self._send(400, "empty text")
            return
        t0 = time.time()
        try:
            synth(text, voices[voice], exaggeration, cfg_weight, temperature, out)
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


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[cbx] ready on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()
