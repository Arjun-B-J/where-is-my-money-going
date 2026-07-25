"""Making stdout able to carry the characters this app prints.

Windows consoles default to a legacy codepage — cp1252 here — which cannot encode
`₹`, box-drawing characters, or an emoji that arrived in a payee name. Printing one
raises `UnicodeEncodeError`, and inside `logging` that surfaces as an unhelpful
`--- Logging error ---` block instead of the message you wanted.

Since every amount this app prints carries a rupee sign, and payee names come from
bank statements and can contain anything, both the CLI and the API server call
this at startup.
"""
from __future__ import annotations

import sys


def use_utf8_streams() -> bool:
    """Reconfigure stdout and stderr to UTF-8. Returns False if not possible.

    A False return is not fatal — callers fall back to ASCII decorations. It
    happens when the streams have been replaced by something without
    `reconfigure`, which is common under test capture and some process managers.
    """
    ok = True
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            ok = False
            continue
        try:
            # `errors="replace"` is the safety net: if a stream still cannot take
            # a character, print a replacement rather than raising mid-report.
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            ok = False
    return ok
