"""Warm F5-TTS server: loads the model once, synthesizes on demand.

POST /say   body = text to speak (raw UTF-8)
            optional query: ?nfe=16&speed=1.0&out=C:\\path\\reply.wav
                            &chime=default  (prepend a chime; off by default)
GET  /health -> "ok" once the model is loaded
"""

import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

warnings.filterwarnings("ignore")

HOST, PORT = "127.0.0.1", 8765

# Second engine: warm Chatterbox server (chatterbox_server.py) in venv-chatterbox.
# Requests with ?engine=chatterbox are proxied here; it writes the out wav itself.
CBX_URL = "http://127.0.0.1:8766"

# Voices and chimes are discovered from the voices/ and chimes/ folders by
# voicelib (the filesystem is the registry -- drop a file, it's available). F5
# needs a "<name>.txt" transcript sidecar next to each voice; a voice without one
# works on Chatterbox but 400s here.
import httputil
import voicelib

BASE = voicelib.BASE
SCRATCH = os.path.join(BASE, "scratch")
os.makedirs(SCRATCH, exist_ok=True)
DEFAULT_VOICE = "doctor"
DEFAULT_OUT = os.path.join(SCRATCH, "reply.wav")

def prepend_chime(chime, speech_wav):
    """Prepend a chime to the speech file in place. Chime and speech can have
    different sample rates/channels; ffmpeg resamples both to 24k mono. On any
    failure the speech file is left untouched (better no chime than no audio)."""
    tmp = speech_wav + ".ic.wav"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                chime,
                "-i",
                speech_wav,
                "-filter_complex",
                "[0:a]aformat=sample_rates=24000:channel_layouts=mono[a0];"
                "[1:a]aformat=sample_rates=24000:channel_layouts=mono[a1];"
                "[a0][a1]concat=n=2:v=0:a=1[a]",
                "-map",
                "[a]",
                "-c:a",
                "pcm_s16le",
                tmp,
            ],
            check=True,
            capture_output=True,
        )
        os.replace(tmp, speech_wav)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def resolve_voice(name):
    """Return (ref_audio_path, ref_text) for a discovered voice. F5 needs the
    transcript, so a voice with no "<name>.txt" sidecar is rejected here (it
    still works on Chatterbox, which ignores the transcript)."""
    audio = voicelib.voices()[name]
    text = voicelib.transcript(audio)
    if text is None:
        raise ValueError(
            f"voice '{name}' has no transcript sidecar "
            f"({os.path.splitext(os.path.basename(audio))[0]}.txt) -- required for F5"
        )
    return audio, text


def proxy_chatterbox(path, text):
    """Forward a /say request to the Chatterbox engine server. It reads voice/
    exaggeration/cfg/out straight from the query string and writes the out wav
    itself, so we just pass the same path + body through. Raises on any error
    (connection refused if that server isn't up, or its 4xx/5xx body)."""
    req = urllib.request.Request(
        CBX_URL + path, data=text.encode("utf-8"), method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode("utf-8", "replace")) from e


print("[server] loading F5-TTS model...", flush=True)
_t = time.time()
from f5_tts.api import F5TTS

TTS = F5TTS(model="F5TTS_v1_Base")
# One shared model instance -> serialize inference. The HTTP layer stays
# threaded (connections queue), but only one /say synthesizes at a time,
# so overlapping requests wait instead of racing and wedging the model.
INFER_LOCK = threading.Lock()
print(f"[server] model loaded in {time.time()-_t:.1f}s", flush=True)

# Warm up so the first real request is fast too.
try:
    _rf, _rt = resolve_voice(DEFAULT_VOICE)
    TTS.infer(
        _rf,
        _rt,
        "System online.",
        nfe_step=16,
        file_wave=DEFAULT_OUT,
        show_info=lambda *a, **k: None,
    )
    print("[server] warmup complete", flush=True)
except Exception as e:  # pragma: no cover
    print(f"[server] warmup failed: {e}", flush=True)


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
        engine = q.get("engine", ["f5"])[0]
        out = q.get("out", [DEFAULT_OUT])[0]
        chime = q.get("chime", [None])[0]
        chimes = voicelib.chimes()
        if chime is not None and chime not in chimes:
            self._send(400, f"unknown chime: {chime}")
            return
        n = int(self.headers.get("Content-Length", 0))
        text = self.rfile.read(n).decode("utf-8").strip()
        if not text:
            self._send(400, "empty text")
            return
        t0 = time.time()
        if engine == "chatterbox":
            # Chatterbox runs in its own venv/process; it validates ?voice= and
            # writes `out` itself. We still own the chime step below.
            try:
                proxy_chatterbox(self.path, text)
            except Exception as e:
                self._send(502, f"chatterbox: {e}")
                return
        elif engine == "f5":
            try:
                nfe = httputil.int_param(q, "nfe", 16)
                speed = httputil.float_param(q, "speed", 1.0)
            except ValueError as e:
                self._send(400, str(e))
                return
            voice = q.get("voice", [DEFAULT_VOICE])[0]
            if voice not in voicelib.voices():
                self._send(400, f"unknown voice: {voice}")
                return
            try:
                ref_file, ref_text = resolve_voice(voice)
                with INFER_LOCK:
                    TTS.infer(
                        ref_file,
                        ref_text,
                        text,
                        nfe_step=nfe,
                        speed=speed,
                        file_wave=out,
                        show_info=lambda *a, **k: None,
                    )
            except Exception as e:
                self._send(500, f"error: {e}")
                return
        else:
            self._send(400, f"unknown engine: {engine}")
            return
        if chime is not None:
            try:
                prepend_chime(chimes[chime], out)
            except Exception as e:
                self._send(500, f"chime error: {e}")
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
    print(f"[server] ready on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()
