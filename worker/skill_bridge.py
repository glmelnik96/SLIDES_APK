"""Skill bridge — makes vendored skill scripts importable from worker code.

The skill (`skill_assets/scripts/*`) is vendored verbatim from the original
Claude.app skill: flat directory, cross-imports like `from build_v5 import …`.
We don't patch those imports — instead we prepend the directory to `sys.path`
and pre-set `CLOUD_RU_TEMPLATE` so `template_path.resolve_template()` finds
the vendored template without consulting the user's home dir.

Call `install()` once per process. Idempotent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_ASSETS = REPO_ROOT / "skill_assets"
SKILL_SCRIPTS = SKILL_ASSETS / "scripts"
SKILL_BRAND = SKILL_ASSETS / "brand"
SKILL_DICTIONARIES = SKILL_ASSETS / "dictionaries"
TEMPLATE_PATH = SKILL_ASSETS / "Cloud.ru_Template_2026.pptx"
DONOR_SLOT_MAP = SKILL_BRAND / "donor-slot-map.yaml"

_installed = False


def install() -> None:
    """Idempotently mount the skill on sys.path and set CLOUD_RU_TEMPLATE."""
    global _installed
    if _installed:
        return
    if not SKILL_SCRIPTS.is_dir():
        raise RuntimeError(f"skill scripts missing: {SKILL_SCRIPTS}")
    if not TEMPLATE_PATH.is_file():
        raise RuntimeError(f"template missing: {TEMPLATE_PATH}")
    s = str(SKILL_SCRIPTS)
    if s not in sys.path:
        sys.path.insert(0, s)
    os.environ.setdefault("CLOUD_RU_TEMPLATE", str(TEMPLATE_PATH))
    _installed = True
