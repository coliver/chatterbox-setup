"""Shared voice/chime discovery for the F5 and Chatterbox servers.

The filesystem IS the registry — no hardcoded lists to keep in sync:

  * Drop an audio file in voices/  -> a voice named after the file (sans extension).
  * Drop an audio file in chimes/  -> a chime named the same way.
  * Optional <name>.txt beside a voice = its F5 reference transcript. F5 needs one;
    Chatterbox clones from the bare audio and ignores it.

Discovery runs per request, so a newly dropped file works immediately — no reboot.
Pure stdlib, so both venvs (F5 and venv-chatterbox) can import it.
"""

import os

BASE = os.path.dirname(os.path.abspath(__file__))
VOICES_DIR = os.path.join(BASE, "voices")
CHIMES_DIR = os.path.join(BASE, "chimes")

# Recognized audio formats, in preference order: when one name exists in several
# formats (e.g. jarvis.wav + jarvis.mp3), the earliest-listed one wins.
_AUDIO_EXTS = (".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus")
_EXT_RANK = {ext: i for i, ext in enumerate(_AUDIO_EXTS)}


def _discover(folder):
    """Map name -> best audio path in `folder` (name = filename without extension).
    Returns {} if the folder doesn't exist yet (e.g. a fresh clone before any
    voice/chime is dropped in)."""
    best = {}
    try:
        entries = os.listdir(folder)
    except FileNotFoundError:
        return best
    for fn in entries:
        stem, ext = os.path.splitext(fn)
        ext = ext.lower()
        if ext not in _EXT_RANK:
            continue  # skip .txt sidecars and anything non-audio
        cur = best.get(stem)
        if cur is None or _EXT_RANK[ext] < _EXT_RANK[os.path.splitext(cur)[1].lower()]:
            best[stem] = os.path.join(folder, fn)
    return best


def voices():
    """name -> reference audio path, discovered from voices/."""
    return _discover(VOICES_DIR)


def chimes():
    """name -> chime audio path, discovered from chimes/."""
    return _discover(CHIMES_DIR)


def transcript(audio_path):
    """F5 reference transcript from the '<name>.txt' sidecar, or None if absent."""
    sidecar = os.path.splitext(audio_path)[0] + ".txt"
    if os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as f:
            return f.read().strip()
    return None
