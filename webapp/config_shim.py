"""Set placeholder env vars BEFORE the engine imports bot.config.get_settings().

bot.config.Settings requires TELEGRAM_BOT_TOKEN. This standalone app does not use
Telegram, so we inject a placeholder. The user only supplies CLOUDRU_API_KEY (.env).
Call apply() at process start, before importing any engine module.
"""
from __future__ import annotations

import os
import sys

_PLACEHOLDERS = {
    "TELEGRAM_BOT_TOKEN": "unused",
}


def apply() -> None:
    if not os.environ.get("CLOUDRU_API_KEY"):
        print("ERROR: CLOUDRU_API_KEY missing. Put it in .env.", file=sys.stderr)
        raise SystemExit(2)
    for key, value in _PLACEHOLDERS.items():
        os.environ.setdefault(key, value)
