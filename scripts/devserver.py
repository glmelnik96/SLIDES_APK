"""Interactive local run — App2 as if it were behind the gateway.

Behind the real gateway, requests arrive authenticated (X-User-Id injected) and
under the /slides prefix. Locally there is no gateway, so a plain `python -m
webapp` returns 401 on every API call and (if .env sets APP_PREFIX=/slides) emits
broken localhost URLs. This launcher fills that gap for hands-on testing before a
redeploy:

  * DEV_USER_ID — auth falls back to a fixed local user (no 401), so the UI is
    fully usable: real build, chat edit, PNG export, history.
  * APP_PREFIX="" — the browser lives at http://127.0.0.1:<port>/ (no prefix).
  * CLOUDRU_API_KEY still comes from .env (loaded by the normal startup shim).

These are os.environ overrides set BEFORE the app imports, so they take priority
over .env. Production is unaffected — this file is never used by app2.service.

Run:  python scripts/devserver.py [--port 8000] [--user local-dev]
"""
from __future__ import annotations

import argparse
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="Run App2 locally (gateway emulated).")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--user", default="local-dev",
                    help="value injected as the current user id (skips gateway auth)")
    args = ap.parse_args()

    # Override before importing the app so pydantic Settings picks these up.
    os.environ["DEV_USER_ID"] = args.user
    os.environ["APP_PREFIX"] = ""           # browser at root, no /slides prefix
    os.environ["HOST"] = args.host
    os.environ["PORT"] = str(args.port)

    import uvicorn
    print(f"App2 (local, gateway-emulated as user '{args.user}') -> "
          f"http://{args.host}:{args.port}/")
    uvicorn.run("webapp.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
