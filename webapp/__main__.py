"""Run: python -m webapp

Host/port come from config (HOST/PORT env / .env). Binds 127.0.0.1 by default:
the gateway is the only public entrypoint and reaches this upstream over loopback
on the same VM (integration contract §1). Do not bind 0.0.0.0 in production.
"""
from __future__ import annotations

import uvicorn

from webapp.config import settings
from webapp.logging_setup import configure_service_logging


def main() -> None:
    configure_service_logging()      # уровни логов видны journalctl -p warning
    uvicorn.run("webapp.app:app", host=settings.host, port=settings.port,
                reload=False)


if __name__ == "__main__":
    main()
