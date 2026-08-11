"""Tiny shared helpers for parsing query-string params, used by both the F5 and
Chatterbox servers. Pure stdlib so it imports cleanly in either venv, and (unlike
importing a server module) importing this does not load a TTS model -- so the
parsing logic can be unit-tested on its own.

`q` is the dict returned by urllib.parse.parse_qs (name -> list of values).
Each helper returns `default` when the param is absent, parses it when present,
and raises ValueError with a clear message on a malformed value (callers turn
that into an HTTP 400 instead of an unhandled crash).
"""


def int_param(q, name, default):
    """Return int-valued query param `name`, or `default` if absent."""
    values = q.get(name)
    if not values:
        return default
    raw = values[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer, got {raw!r}")


def float_param(q, name, default):
    """Return float-valued query param `name`, or `default` if absent."""
    values = q.get(name)
    if not values:
        return default
    raw = values[0]
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {raw!r}")
