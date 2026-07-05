"""A/B eval harness: compare LLM models on the htmlslides deck-building pipeline.

Runs the real production pipeline (build_deck + polish_plan, same path as
worker/tasks/htmlnew.py) for each (model, input-document) pair, collects
per-stage metrics, and produces a comparative report.

IMPORTANT -- experiment fairness:
  The TESTED model drives planner + filler + autofix.
  The JUDGE model (fixed, default Kimi-K2.6) drives vision-QA inside the
  pipeline AND the final judge scoring after the build.  The judge is
  substituted via a monkeypatch wrapper around review_slide() -- the
  production code is never modified.

Example:
    python scripts/model_eval.py \\
        --models "moonshotai/Kimi-K2.6,MiniMax-M1-80k" \\
        --inputs scripts/eval_inputs \\
        --out   scripts/eval_out \\
        --judge-model moonshotai/Kimi-K2.6 \\
        --runs 1

Dependencies: the same virtualenv that runs the webapp (pip install -e .[qa]).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap: .env + sys.path
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

def _load_dotenv() -> None:
    """Load .env from repo root (same logic as htmlslides.cli)."""
    env = _REPO / ".env"
    if not env.is_file():
        return
    for line in env.read_text("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"'))

_load_dotenv()

# ---------------------------------------------------------------------------
# Pipeline imports (after sys.path and dotenv)
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field

from htmlslides.pipeline.build import build_deck
from htmlslides.pipeline.client import KimiClient, LLMFormatError
from htmlslides.pipeline.linter import lint_html
from htmlslides.pipeline.screenshot import QAUnavailable, screenshot_slides
from htmlslides.pipeline.vision_qa import review_slide as _original_review_slide


# ---------------------------------------------------------------------------
# Pydantic model for the final judge scoring rubric
# ---------------------------------------------------------------------------
class SlideJudgeScore(BaseModel):
    """Judge verdict per slide (4 criteria, 1-10 each)."""
    layout: int = Field(ge=1, le=10, description="Layout quality, no overflow")
    readability: int = Field(ge=1, le=10, description="Text readability and contrast")
    relevance: int = Field(ge=1, le=10, description="Content matches the brief")
    aesthetics: int = Field(ge=1, le=10, description="Visual aesthetics, brand feel")
    issues: list[str] = Field(default_factory=list)

    @property
    def mean(self) -> float:
        return (self.layout + self.readability + self.relevance + self.aesthetics) / 4.0


JUDGE_RUBRIC_SYSTEM = """\
You are a presentation-slide quality judge.  Given a PNG screenshot of one
slide and its content brief, rate it on four criteria (1 = terrible, 10 = perfect).
Return ONLY JSON: {"layout":N,"readability":N,"relevance":N,"aesthetics":N,"issues":["..."]}

Criteria:
- layout: no text overflow, no element collisions, proper margins and spacing.
- readability: text is legible, good contrast, appropriate font sizes.
- relevance: slide content faithfully reflects the brief (facts, numbers, names).
- aesthetics: visual polish, brand consistency, colour harmony, whitespace balance.

List concrete issues (if any) in "issues".  Be strict but fair."""


# ---------------------------------------------------------------------------
# Metrics collection data classes
# ---------------------------------------------------------------------------
@dataclass
class StageTimings:
    parse_s: float = 0.0
    plan_s: float = 0.0
    fill_s: float = 0.0
    assemble_s: float = 0.0
    lint_s: float = 0.0
    vision_qa_s: float = 0.0
    autofix_s: float = 0.0
    total_s: float = 0.0


@dataclass
class BuildMetrics:
    model: str = ""
    input_file: str = ""
    run_index: int = 1
    timings: StageTimings = field(default_factory=StageTimings)
    slides_count: int = 0
    html_size_bytes: int = 0
    planner_fallbacks: int = 0
    filler_blank_fallbacks: int = 0
    filler_validation_retries: int = 0
    llm_format_errors: int = 0
    lint_issues_final: int = 0
    vision_qa_reviewed_ok: int = 0
    vision_qa_total: int = 0
    qa_flagged_slides: int = 0
    vision_qa_fixes: dict[int, list[str]] = field(default_factory=dict)
    judge_scores: dict[int, dict] = field(default_factory=dict)
    judge_mean: float = 0.0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Logging interceptor -- captures structured events from planner/filler/client
# ---------------------------------------------------------------------------
_STAGE_PREFIX = re.compile(
    r"^(parse|rebrand|plan|fill|assemble|lint|vision-qa|autofix|done):")

# Map progress-message prefixes to stage-timer fields.
_STAGE_TIMER_MAP: dict[str, str] = {
    "parse": "parse_s",
    "rebrand": "parse_s",
    "plan": "plan_s",
    "fill": "fill_s",
    "assemble": "assemble_s",
    "lint": "lint_s",
    "vision-qa": "vision_qa_s",
    "autofix": "autofix_s",
}


class _MetricsCollector:
    """Accumulates metrics from progress callbacks and structlog events."""

    def __init__(self) -> None:
        self.metrics = BuildMetrics()
        self._stage_start: float = 0.0
        self._current_stage: str = ""
        self._qa_failures: int = 0  # vision-QA calls that threw LLMFormatError
        self._flagged_indices: set[int] = set()  # slides with QA remarks

    def progress_cb(self, message: str) -> None:
        """Passed as the ``progress=`` callback to build_deck."""
        now = time.monotonic()

        # Detect stage transitions from progress messages.
        m = _STAGE_PREFIX.match(message)
        if m:
            stage = m.group(1)
            self._close_stage(now)
            self._current_stage = stage
            self._stage_start = now

        # Count planner fallbacks from progress warns.
        # (structlog logger in planner also logs "planner.section_fallback"
        #  but we intercept via the logging bridge below.)
        if "planner.section_fallback" in message or (
                message.startswith("warn:") and "section_fallback" in message):
            self.metrics.planner_fallbacks += 1

        # Count filler blank fallbacks.
        if "blank" in message.lower() and "warn:" in message.lower():
            self.metrics.filler_blank_fallbacks += 1

        # Filler validation retries are logged by the filler as
        # "warn: slide N validation retry" -- but actually the retry happens
        # inside _fill_template silently. We count LLMFormatError warnings.
        # The filler emits FillError wrapping LLMFormatError, so the progress
        # message contains "invalid json" rather than "LLMFormatError".
        if "LLMFormatError" in message or "invalid json" in message.lower() or (
                "format" in message.lower() and "warn" in message.lower()):
            self.metrics.llm_format_errors += 1

        # Vision-QA results.
        # "vision-qa: слайд N" = a slide is being reviewed.
        if message.startswith("vision-qa:"):
            self.metrics.vision_qa_total += 1
        # "warn: vision-qa слайда N не удался" = review threw LLMFormatError.
        if message.startswith("warn: vision-qa"):
            self._qa_failures += 1

        # qa_flagged_slides: count slides that got QA remarks (lint+bbox+vision).
        # Primary signal: "autofix: исправляю слайды [1, 3, 5] (1 круг)"
        autofix_m = re.match(r"autofix: исправляю слайды \[([0-9, ]+)\]", message)
        if autofix_m:
            indices = [int(x.strip()) for x in autofix_m.group(1).split(",")
                       if x.strip()]
            self._flagged_indices.update(indices)
        # Fallback signal (max_autofix=0 path): "warn: slide N: ..."
        warn_slide_m = re.match(r"warn: slide (\d+):", message)
        if warn_slide_m:
            self._flagged_indices.add(int(warn_slide_m.group(1)))

    def _close_stage(self, now: float) -> None:
        if self._current_stage and self._stage_start:
            elapsed = now - self._stage_start
            field_name = _STAGE_TIMER_MAP.get(self._current_stage, "")
            if field_name:
                old = getattr(self.metrics.timings, field_name, 0.0)
                setattr(self.metrics.timings, field_name, old + elapsed)

    def finalize(self, now: float) -> None:
        self._close_stage(now)
        # vision-QA reviewed_ok = total reviewed minus those that threw errors.
        # (Slides that received fixes still count as "reviewed successfully".)
        self.metrics.vision_qa_reviewed_ok = max(
            0, self.metrics.vision_qa_total - self._qa_failures)
        self.metrics.qa_flagged_slides = len(self._flagged_indices)


class _StructlogInterceptHandler(logging.Handler):
    """Logging handler that intercepts structlog output from the planner logger.

    structlog in this codebase uses stdlib logging as the final output,
    so we can tap into events via a standard Handler attached to the
    'htmlslides.pipeline.planner' logger name.
    """

    def __init__(self, collector: _MetricsCollector) -> None:
        super().__init__()
        self.collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "section_fallback" in msg:
            self.collector.metrics.planner_fallbacks += 1
        if "retry_no_think" in msg:
            self.collector.metrics.llm_format_errors += 1


# ---------------------------------------------------------------------------
# Judge-model monkeypatch for vision-QA fairness
# ---------------------------------------------------------------------------
def _make_judge_review(judge_client: KimiClient):
    """Return a patched review_slide that uses the judge_client instead of the
    build client.  This is injected into htmlslides.pipeline.vision_qa.review_slide
    at runtime (and restored after each build), so the production code is untouched.

    The wrapper signature matches the original:
        review_slide(client, png_path, *, brief, theme, max_tokens) -> QAVerdict

    The ``client`` arg (which would be the test-model client) is IGNORED;
    the judge_client is used instead.
    """
    def _patched_review_slide(client, png_path, *, brief, theme="dark",
                              max_tokens=12288):
        return _original_review_slide(judge_client, png_path,
                                      brief=brief, theme=theme,
                                      max_tokens=max_tokens)
    return _patched_review_slide


# ---------------------------------------------------------------------------
# Core: run one build
# ---------------------------------------------------------------------------
def run_one_build(
    model: str,
    input_path: Path,
    out_dir: Path,
    judge_client: KimiClient,
    run_index: int = 1,
) -> BuildMetrics:
    """Run the full pipeline for one (model, input) pair and return metrics."""
    import htmlslides.pipeline.vision_qa as _vqa_module

    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    collector = _MetricsCollector()
    collector.metrics.model = model
    collector.metrics.input_file = input_path.name
    collector.metrics.run_index = run_index

    # Install structlog interceptor on the planner logger.
    planner_logger = logging.getLogger("htmlslides.pipeline.planner")
    handler = _StructlogInterceptHandler(collector)
    planner_logger.addHandler(handler)

    # Monkeypatch vision-QA to use judge client.
    _original_fn = _vqa_module.review_slide
    _vqa_module.review_slide = _make_judge_review(judge_client)

    t0 = time.monotonic()
    try:
        test_client = KimiClient(model=model)
        deck_html_path = build_deck(
            input_path,
            out_dir / "deck.html",
            mode="auto",
            vision=True,
            freeform_ok=True,
            theme="dark",
            max_autofix=1,
            keep_artifacts=str(artifacts_dir),
            client=test_client,
            progress=collector.progress_cb,
        )

        # Read the produced HTML for metrics.
        html_text = deck_html_path.read_text("utf-8")
        collector.metrics.html_size_bytes = len(html_text.encode("utf-8"))
        collector.metrics.slides_count = len(
            re.findall(r'<section[^>]*class="[^"]*\bslide\b', html_text))

        # Lint issues (pre-autofix count was already emitted via progress;
        # we re-lint the final HTML to get the residual count).
        final_lint = lint_html(html_text)
        collector.metrics.lint_issues_final = len(final_lint)

        # Vision-QA passed count: read from artifacts if available.
        # The pipeline itself logs vision-qa per slide; we counted in progress.
        # Count passed = total - slides that got fixes.
        # (We don't have direct access to QAVerdict here; the pipeline
        #  already ran. Use the progress-based counts.)

    except Exception as exc:
        collector.metrics.error = f"{type(exc).__name__}: {exc}"
        # Print traceback for diagnostics but do NOT abort the entire eval.
        traceback.print_exc()
    finally:
        t1 = time.monotonic()
        collector.finalize(t1)
        collector.metrics.timings.total_s = t1 - t0
        # Restore original review_slide.
        _vqa_module.review_slide = _original_fn
        planner_logger.removeHandler(handler)

    return collector.metrics


# ---------------------------------------------------------------------------
# Post-build judge scoring
# ---------------------------------------------------------------------------
def judge_deck(
    deck_html: Path,
    slides_count: int,
    judge_client: KimiClient,
    out_dir: Path,
) -> dict[int, SlideJudgeScore]:
    """Render PNGs of every slide and score each via the judge model."""
    if slides_count == 0 or not deck_html.is_file():
        return {}

    png_dir = out_dir / "judge_png"
    png_dir.mkdir(parents=True, exist_ok=True)
    indices = list(range(1, slides_count + 1))

    try:
        pngs = screenshot_slides(deck_html, indices, png_dir)
    except QAUnavailable as exc:
        print(f"  [judge] screenshots unavailable: {exc}", flush=True)
        return {}

    # Read the deckfill artifact (dumped after fill, content populated).
    # Fallback to deckplan.json (pre-fill, content empty) if deckfill is missing.
    briefs: dict[int, str] = {}
    fill_path = out_dir / "artifacts" / "deckfill.json"
    plan_path = out_dir / "artifacts" / "deckplan.json"
    brief_source = fill_path if fill_path.is_file() else plan_path
    if brief_source.is_file():
        try:
            plan_data = json.loads(brief_source.read_text("utf-8"))
            for slide in plan_data.get("slides", []):
                idx = slide.get("index", 0)
                content = slide.get("content", {})
                briefs[idx] = json.dumps(content, ensure_ascii=False)[:2000]
        except Exception:
            pass

    scores: dict[int, SlideJudgeScore] = {}
    from htmlslides.pipeline.client import image_part

    for index in indices:
        png_path = pngs.get(index)
        if png_path is None or not png_path.is_file():
            continue
        brief = briefs.get(index, f"Slide {index}")
        user_text = f"Content brief:\n{brief}"
        messages = [
            {"role": "system", "content": JUDGE_RUBRIC_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                image_part(png_path),
            ]},
        ]
        try:
            score = judge_client.chat_json(messages, SlideJudgeScore,
                                           max_tokens=4096)
            scores[index] = score
            print(f"  [judge] slide {index}: "
                  f"L={score.layout} R={score.readability} "
                  f"V={score.relevance} A={score.aesthetics} "
                  f"mean={score.mean:.1f}", flush=True)
        except (LLMFormatError, Exception) as exc:
            print(f"  [judge] slide {index} failed: {exc}", flush=True)

    return scores


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def _model_slug(model: str) -> str:
    """Filesystem-safe slug: 'moonshotai/Kimi-K2.6' -> 'moonshotai_Kimi-K2.6'."""
    return re.sub(r"[^\w\-.]", "_", model)


def generate_report(all_metrics: list[BuildMetrics], out_dir: Path) -> None:
    """Write report.json and REPORT.md."""
    # --- JSON ---
    report_data: list[dict] = []
    for m in all_metrics:
        entry = {
            "model": m.model,
            "input_file": m.input_file,
            "run_index": m.run_index,
            "error": m.error,
            "timings": {
                "total_s": round(m.timings.total_s, 1),
                "parse_s": round(m.timings.parse_s, 1),
                "plan_s": round(m.timings.plan_s, 1),
                "fill_s": round(m.timings.fill_s, 1),
                "assemble_s": round(m.timings.assemble_s, 1),
                "lint_s": round(m.timings.lint_s, 1),
                "vision_qa_s": round(m.timings.vision_qa_s, 1),
                "autofix_s": round(m.timings.autofix_s, 1),
            },
            "slides_count": m.slides_count,
            "html_size_bytes": m.html_size_bytes,
            "planner_fallbacks": m.planner_fallbacks,
            "filler_blank_fallbacks": m.filler_blank_fallbacks,
            "llm_format_errors": m.llm_format_errors,
            "lint_issues_final": m.lint_issues_final,
            "vision_qa_reviewed_ok": m.vision_qa_reviewed_ok,
            "vision_qa_total": m.vision_qa_total,
            "qa_flagged_slides": m.qa_flagged_slides,
            "judge_scores": {
                str(k): v for k, v in m.judge_scores.items()
            },
            "judge_mean": round(m.judge_mean, 2),
        }
        report_data.append(entry)

    report_json = out_dir / "report.json"
    report_json.write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"[report] {report_json}", flush=True)

    # --- Markdown ---
    lines: list[str] = [
        "# Model Eval Report",
        "",
        "## Summary",
        "",
        "| Model | Input | Run | Time(s) | Slides | Fallbacks(plan) "
        "| Blanks(fill) | LLM Errors | Lint(final) | QA ok/total | QA flagged | Judge |",
        "|-------|-------|-----|---------|--------|---------------"
        "|--------------|------------|-------------|-------------|------------|-------|",
    ]
    for m in all_metrics:
        qa = (f"{m.vision_qa_reviewed_ok}/{m.vision_qa_total}"
              if m.vision_qa_total else "n/a")
        judge = f"{m.judge_mean:.1f}" if m.judge_mean else ("ERR" if m.error else "n/a")
        status = "ERR" if m.error else ""
        lines.append(
            f"| {m.model} | {m.input_file} | {m.run_index} "
            f"| {m.timings.total_s:.0f} | {m.slides_count} "
            f"| {m.planner_fallbacks} | {m.filler_blank_fallbacks} "
            f"| {m.llm_format_errors} | {m.lint_issues_final} "
            f"| {qa} | {m.qa_flagged_slides} | {judge} {status}|"
        )

    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- **extra_body (no-think)**: The planner and filler hardcode "
                 "`{\"thinking\": {\"type\": \"disabled\"}}` in extra_body for "
                 "Kimi-K2.6.  Other models may reject this parameter or ignore it.  "
                 "If the API returns an error on the unknown extra_body field, that "
                 "is captured as a build error for the tested model -- this is a "
                 "valid experimental finding (model incompatibility with the "
                 "pipeline's Kimi-specific tuning).")
    lines.append("- **Judge model**: vision-QA inside the pipeline and the final "
                 "judge scoring both use the fixed judge model (default Kimi-K2.6), "
                 "ensuring fair comparison across tested models.")
    lines.append("- **Sequential runs**: builds for different models run "
                 "SEQUENTIALLY to avoid RPS contention on the Cloud.ru API.")
    lines.append("")

    # Errors section.
    errors = [m for m in all_metrics if m.error]
    if errors:
        lines.append("## Errors")
        lines.append("")
        for m in errors:
            lines.append(f"- **{m.model}** / {m.input_file} run {m.run_index}: "
                         f"`{m.error}`")
        lines.append("")

    report_md = out_dir / "REPORT.md"
    report_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] {report_md}", flush=True)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="A/B eval harness for LLM models in the htmlslides pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument(
        "--models", required=True,
        help="Comma-separated model IDs to test (e.g. 'moonshotai/Kimi-K2.6,other-model')")
    ap.add_argument(
        "--inputs", default=str(_REPO / "scripts" / "eval_inputs"),
        help="Directory with .md input documents (default: scripts/eval_inputs)")
    ap.add_argument(
        "--out", default=str(_REPO / "scripts" / "eval_out"),
        help="Output directory (default: scripts/eval_out)")
    ap.add_argument(
        "--judge-model", default="moonshotai/Kimi-K2.6",
        help="Model for vision-QA judging (default: moonshotai/Kimi-K2.6)")
    ap.add_argument(
        "--runs", type=int, default=1,
        help="Number of runs per (model, input) pair (default: 1)")
    ap.add_argument(
        "--no-judge", action="store_true",
        help="Skip the final judge scoring (faster, no PNG rendering)")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    inputs_dir = Path(args.inputs)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not inputs_dir.is_dir():
        print(f"ERROR: inputs directory not found: {inputs_dir}", flush=True)
        return 1

    input_files = sorted(inputs_dir.glob("*.md"))
    if not input_files:
        print(f"ERROR: no .md files in {inputs_dir}", flush=True)
        return 1

    print(f"Models: {models}", flush=True)
    print(f"Inputs: {[f.name for f in input_files]}", flush=True)
    print(f"Runs per pair: {args.runs}", flush=True)
    print(f"Judge model: {args.judge_model}", flush=True)
    print(f"Output: {out_dir}", flush=True)
    print("=" * 60, flush=True)

    # Create the judge client once (shared across all builds).
    judge_client = KimiClient(model=args.judge_model)

    all_metrics: list[BuildMetrics] = []

    # SEQUENTIAL: one model at a time, one input at a time.
    for model in models:
        slug = _model_slug(model)
        for input_path in input_files:
            stem = input_path.stem
            for run_idx in range(1, args.runs + 1):
                run_label = f"run{run_idx}" if args.runs > 1 else ""
                sub_dir = out_dir / slug / stem
                if run_label:
                    sub_dir = sub_dir / run_label

                print(f"\n{'='*60}", flush=True)
                print(f"BUILD: model={model}  input={input_path.name}  "
                      f"run={run_idx}/{args.runs}", flush=True)
                print(f"  -> {sub_dir}", flush=True)
                print(f"{'='*60}", flush=True)

                metrics = run_one_build(
                    model=model,
                    input_path=input_path,
                    out_dir=sub_dir,
                    judge_client=judge_client,
                    run_index=run_idx,
                )

                # Final judge scoring.
                if not args.no_judge and metrics.error is None:
                    print("  [judge] scoring slides...", flush=True)
                    deck_html = sub_dir / "deck.html"
                    scores = judge_deck(
                        deck_html, metrics.slides_count, judge_client, sub_dir)
                    metrics.judge_scores = {
                        k: {"layout": s.layout, "readability": s.readability,
                             "relevance": s.relevance, "aesthetics": s.aesthetics,
                             "mean": round(s.mean, 2), "issues": s.issues}
                        for k, s in scores.items()
                    }
                    if scores:
                        metrics.judge_mean = sum(
                            s.mean for s in scores.values()) / len(scores)

                all_metrics.append(metrics)

                # Summary line.
                if metrics.error:
                    print(f"  RESULT: ERROR -- {metrics.error}", flush=True)
                else:
                    print(f"  RESULT: {metrics.slides_count} slides, "
                          f"{metrics.timings.total_s:.0f}s, "
                          f"judge={metrics.judge_mean:.1f}", flush=True)

    # Generate reports.
    print(f"\n{'='*60}", flush=True)
    print("Generating reports...", flush=True)
    generate_report(all_metrics, out_dir)

    print("Done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
