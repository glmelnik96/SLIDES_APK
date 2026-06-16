"""LangGraph nodes for the v0.9 batch pipeline.

Two modules:
- ``agents`` — LLM-driven nodes (Agents 01, 02, 03, 04, 05, 06, 07, 10).
- ``pipeline`` — Python-script-driven nodes (parse, assemble_plan, build,
  brand_guard, render_png, process_verify) — currently skeletons with FIXME
  for the script wiring; finalize_node ties it off.
"""
