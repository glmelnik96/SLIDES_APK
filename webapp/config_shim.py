"""Set placeholder env vars BEFORE the engine imports bot.config.get_settings().

bot.config.Settings requires TELEGRAM_BOT_TOKEN. This standalone app does not use
Telegram, so we inject a placeholder. The user supplies CLOUDRU_API_KEY in .env;
we load that .env into the environment here, before any engine import.
Call apply() at process start, before importing any engine module.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PLACEHOLDERS = {
    "TELEGRAM_BOT_TOKEN": "unused",
}

_DEFAULT_ENV = Path(__file__).resolve().parent.parent / ".env"


def apply(env_path: Path | None = None) -> None:
    path = env_path if env_path is not None else _DEFAULT_ENV
    if path.is_file():
        try:
            from dotenv import load_dotenv
            load_dotenv(path)
        except ImportError:
            pass
    if not os.environ.get("CLOUDRU_API_KEY"):
        print("ERROR: CLOUDRU_API_KEY missing. Put it in .env.", file=sys.stderr)
        raise SystemExit(2)
    for key, value in _PLACEHOLDERS.items():
        os.environ.setdefault(key, value)
