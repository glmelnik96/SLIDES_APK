"""Prompt modules for the v0.9 batch pipeline.

Each module exports:
- ``SYSTEM`` — string for the system message (Russian, WS-E re-engineered
  per `<project>/memory/prompt_adaptation.md`)
- ``build_messages(payload) -> list[dict]`` — assembles the full
  ``messages`` list for ``LLMCall(messages=...)``. Caller passes the
  payload (parsed deck / brief / classification / etc.); the module
  serialises it into the user message body.

Vision-capable modules (01, 10) accept an additional ``images`` arg in
``build_messages`` and return both the messages list and the images
list ready to be passed as ``LLMCall(images=...)``.

The shared Cloud.ru brand glossary lives in `_shared.py`; modules import
constants from there to avoid drift.
"""
from __future__ import annotations
