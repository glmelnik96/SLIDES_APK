"""Run: python -m webapp

Host/port come from config (PORT env / .env). Binds 0.0.0.0 so the gateway on
the same VM can reach the upstream; the gateway is the only public entrypoint.
"""
from __future__ import annotations

import uvicorn

from webapp.config import settings


def main() -> None:
    uvicorn.run("webapp.app:app", host="0.0.0.0", port=settings.port,
                reload=False)


if __name__ == "__main__":
    main()
