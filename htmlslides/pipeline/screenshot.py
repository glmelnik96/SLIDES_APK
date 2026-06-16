"""Скриншоты слайдов и замер переполнения через Playwright (extra [qa]).

Опционален: без playwright бросает QAUnavailable — пайплайн работает без vision-QA.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .linter import LintIssue

try:
    from playwright.sync_api import sync_playwright
except ImportError:                               # extra [qa] не установлен
    sync_playwright = None


class QAUnavailable(RuntimeError):
    """playwright не установлен или браузер недоступен."""


def _require_playwright() -> None:
    if sync_playwright is None:
        raise QAUnavailable(
            "playwright не установлен: pip install 'htmlslides[qa]' "
            "и затем: playwright install chromium")


def _launch(pw):
    try:
        return pw.chromium.launch()
    except Exception as exc:
        raise QAUnavailable(f"chromium недоступен: {exc}") from exc


def screenshot_slides(html_path: str | Path, indices: Iterable[int],
                      out_dir: str | Path, *,
                      viewport: tuple[int, int] = (1920, 1080)) -> dict[int, Path]:
    """Снять PNG слайдов (индексы 1-based) из собранной деки. Для vision-QA."""
    _require_playwright()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    url = Path(html_path).resolve().as_uri()
    shots: dict[int, Path] = {}
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(
            viewport={"width": viewport[0], "height": viewport[1]})
        page.goto(url)
        # deck-chrome (зелёный прогресс-бар) не должен попадать в кадры:
        # vision-QA считает его лишним зелёным акцентом.
        page.add_style_tag(content=".deck-progress{display:none}")
        for index in sorted(set(indices)):
            page.evaluate(f"window.deck.goTo({index - 1})")
            page.wait_for_timeout(900)            # дать motion-входам доиграть (slow-02=700ms)
            png = out / f"qa-slide-{index:02d}.png"
            page.screenshot(path=str(png))
            shots[index] = png
        browser.close()
    return shots


_OVERFLOW_JS = """
(tolerance) => {
  const out = [];
  document.querySelectorAll('.slide').forEach((slide, si) => {
    window.deck.goTo(si);
    const sb = slide.getBoundingClientRect();
    const scale = sb.width / (slide.offsetWidth || 1920);
    // bbox блочного элемента не растёт от непереносимого текста (ink overflow),
    // поэтому правую/нижнюю границы расширяем на scroll-переполнение.
    // Снизу метрики шрифта при line-height 1.0 дают паразитный scrollHeight
    // (порядка +24px у t-kpi-hero) — игнорируем инфляцию меньше 15% высоты строки.
    const bounds = (el, r) => {
      const cs = getComputedStyle(el);
      let lh = parseFloat(cs.lineHeight);
      if (!isFinite(lh)) lh = parseFloat(cs.fontSize) * 1.2;
      const dy = Math.max(0, el.scrollHeight - el.clientHeight);
      return {
        right: r.right + Math.max(0, el.scrollWidth - el.clientWidth) * scale,
        bottom: r.bottom + (dy > 0.15 * lh ? dy : 0) * scale,
      };
    };
    slide.querySelectorAll('*').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      const b = bounds(el, r);
      if (r.left < sb.left - tolerance || b.right > sb.right + tolerance ||
          r.top < sb.top - tolerance || b.bottom > sb.bottom + tolerance) {
        out.push({slide: si + 1, code: 'overflow',
                  detail: String(el.className || el.tagName)});
      }
    });
    if (slide.dataset.template === 'freeform') {
      const sx = sb.width / 1920, sy = sb.height / 1080;
      const safe = {left: sb.left + 60 * sx, right: sb.left + 1860 * sx,
                    top: sb.top + 300 * sy, bottom: sb.top + 1020 * sy};
      // только типографика: класс с префиксом t- (не подстрока — иначе
      // ложно матчатся accent-block, content--center и т.п.)
      slide.querySelectorAll('*').forEach(el => {
        if (![...el.classList].some(c => c.startsWith('t-'))) return;
        if (el.closest('.slide-header')) return;
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;
        const b = bounds(el, r);
        if (r.left < safe.left - tolerance || b.right > safe.right + tolerance ||
            r.top < safe.top - tolerance || b.bottom > safe.bottom + tolerance) {
          out.push({slide: si + 1, code: 'safe_zone',
                    detail: String(el.className)});
        }
      });
    }
  });
  return out;
}
"""


def measure_overflow(html_path: str | Path, *, tolerance: float = 2.0) -> list[LintIssue]:
    """Реальный замер bbox: вылет за слайд (все слайды) и за safe-зону (freeform)."""
    _require_playwright()
    url = Path(html_path).resolve().as_uri()
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1920, "height": 1080},
                                reduced_motion="reduce")
        page.goto(url)
        page.wait_for_timeout(200)
        raw = page.evaluate(_OVERFLOW_JS, tolerance)
        browser.close()
    seen: set[tuple[int, str, str]] = set()
    issues: list[LintIssue] = []
    for item in raw:
        key = (item["slide"], item["code"], item["detail"])
        if key not in seen:
            seen.add(key)
            issues.append(LintIssue(item["slide"], item["code"], item["detail"]))
    return issues
