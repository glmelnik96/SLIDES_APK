"""LLM HTML-authoring for the HTML-render pipeline (Path B).

Given one slide's content payload (the planner's text/native blocks) the
SLIDE_COMPOSER model authors a single ``<div class="slide …">…</div>`` body
using only the brand stylesheet. Hybrid governance: the model designs directly
in HTML/CSS — the medium it has the strongest priors for — but inside a locked
per-archetype recipe (the reference template's actual layouts), policed by the
brand canons in the system prompt, a brand-critic gate and the vision QA loop.

Validated in spike Check 1 (freeform) and hardened 2026-06-09 with the
brand-fidelity batch: grey canvas, true cube mark, rule-points, brand table,
cover/divider motifs, per-archetype routing.
"""
from __future__ import annotations

import json
import re

from llm.client import LLMCall, call_role
from llm.roles import Role

# ── Brand chrome snippets (verbatim — the model copies these) ────────────────

_LOGO_LIGHT = '<div class="brand-logo"><span class="brand-logo__cube"></span>cloud.ru</div>'
_LOGO_DARK = '<div class="brand-logo brand-logo--dark"><span class="brand-logo__cube"></span>cloud.ru</div>'
_COPYRIGHT = ('<div class="copyright">© 2026 Cloud.ru Любое копирование и воспроизведение '
              'содержания (в том числе частичное) без разрешения правообладателя запрещено.</div>')

_SYSTEM = f"""Ты — арт-директор Cloud.ru 2.0. Верстаешь ОДИН слайд 1280×720 на чистом HTML,
используя ТОЛЬКО классы и переменные из приложенного brand.css (плюс точечные inline-стили
на базе var(--…) там, где это разрешено рецептом). Верни ТОЛЬКО HTML-фрагмент:
один <div class="slide ...">…</div>. Без markdown, без ```html, без <html>/<head>.

КАНОНЫ БРЕНДА (нарушение = брак):
1. Плоский дизайн: НИКАКИХ градиентов, теней, скруглений, italic, подчёркиваний, emoji.
2. Лого — СТРОГО этот фрагмент, не меняй ни символа (вордмарк строчными «cloud.ru»):
   светлый фон: {_LOGO_LIGHT}
   тёмный фон:  {_LOGO_DARK}
3. Копирайт внизу — СТРОГО: {_COPYRIGHT}
4. ЗЕЛЁНЫЙ #26D07C — единственный акцент раскладки: тонкие черты-правила (.rule-point,
   .takeaway), квадратные буллеты (.bullets li), рамка заголовка обложки. Зелёного ≤10%
   площади. НЕ заливай зелёным карточки/плашки/текст (исключения: фон cover_green;
   шапка .brand-table; зелёный заголовок на тёмной обложке).
   ИСКЛЮЧЕНИЕ data-viz: в таблицах и графиках разрешены доп. цвета (синяя акцент-колонка
   var(--blue), пастельные тинты рядов).
5. КОНТЕНТНЫЕ слайды: канвас СЕРЫЙ — <div class="slide slide--canvas">. Контент лежит
   белыми карточками (.card) и/или блоками с зелёной чертой (.rule-point) ПОВЕРХ серого.
   Чисто-белый фон канваса у контентного слайда — брак. Обложки/разделители — свои фоны.
6. Заголовок слайда = .slide__header (CAPS, верх слева). Текст графитовый #222,
   вторичный — var(--text-gray).
7. ВЕРТИКАЛЬНОЕ РАСПРЕДЕЛЕНИЕ: контент занимает безопасную зону top:150 … bottom:64
   ЦЕЛИКОМ. Не прижимай всё к верху, не оставляй пустую нижнюю половину. Используй
   .content-body (space-between), .grid (row-gap) или .takeaway внизу. Но и не выжимай
   за нижний край: последняя строка ≥64px от низа.
   ЕСЛИ на слайде есть .takeaway — она position:absolute у низа: основной контент-
   контейнер ОБЯЗАН заканчиваться на bottom:140px (НЕ 64px), иначе черта ляжет
   поверх последнего ряда. Никогда не накладывай .takeaway на карточки/текст.
8. Текст бери ДОСЛОВНО из контента: не переписывай, не переводи, не сокращай и не
   добавляй своего. Если пунктов слишком много для красивой раскладки — уменьшай кегль
   или дели на 2 колонки, но НЕ выбрасывай текст.
9. ПОРЯДОК ПАР СВЯЩЕНЕН: если body — чередование «заголовок (часто с двоеточием)» и
   следующего за ним текста-расшифровки, пары образуются СТРОГО по порядку списка.
   Не перетасовывай: заголовок №3 получает текст, идущий сразу после него, а не чужой.

РЕЦЕПТЫ ПО АРХЕТИПАМ (поле "archetype" в контенте — выбери свой рецепт):

• cover (обложка, первый слайд):
  <div class="slide cover-green"> + .logo-plate (белая плашка с лого в левом верхнем углу),
  .display-title (CAPS графит, ≤3 строк; при длинном заголовке уменьшай font-size inline
  до 72–96px, чтобы влезло), под ним графитовая плашка спикера/подзаголовка
  (фон var(--grey3), текст var(--green), padding 24px 30px). Без .slide__header.

• section-divider (разделитель/титул раздела) — тёмный, богатый мотивами:
  <div class="slide cover-dark"> + лого тёмной версии сверху слева:
  <div class="brand-logo brand-logo--dark brand-logo--tl">…</div>, .display-title (зелёный текст в зелёной рамке; при длинном
  заголовке уменьшай font-size inline до 56–72px), .subtitle-plate (серая плашка с
  зелёным подзаголовком — если есть подзаголовок/key_phrase), справа от плашки — ряд
  из 3–4 <div class="bracket" style="border-color:var(--stroke)"></div> внутри
  <div class="brackets" style="right:35px; top:470px">, внизу полоса точек:
  <div class="dot-grid" style="left:0; right:0; bottom:30px; height:130px;
  background-image:radial-gradient(var(--green) 1px, transparent 1px); opacity:0.5">.
  Без .slide__header, без серого канваса.

• title-body (текстовый слайд):
  слайд .slide--canvas; ЕСЛИ пункты — пары «заголовок: расшифровка» → раскладка
  .grid с --cols:2|3 из .rule-point (черта→жирный h3→серый p). ЕСЛИ плоский список
  фраз → .bullets ul (зелёные квадратные буллеты) внутри .content-body. ЕСЛИ есть
  key_phrase — вынеси её в .takeaway внизу. 5+ пунктов: 2 колонки.
  .takeaway добавляй ТОЛЬКО если key_phrase несёт новый смысл: если она дублирует
  заголовок слайда (или почти совпадает с ним) — НЕ выводи её вообще.

• comparison / kpi (колонки, метрики):
  слайд .slide--canvas; .grid c --cols:2|3; каждая ячейка = .rule-point (для KPI: h3 —
  крупная цифра 56–72px графитом, p — подпись серым). Не более 8 ячеек.

• table:
  слайд .slide--canvas; <table class="brand-table"> — зелёная шапка th, зебра-тело;
  ровно ОДНА смысловая колонка может получить class="accent" (th и td) — выбери
  столбец-вывод/итог. Колонок ≤6, строк ≤8; при 7+ строках font-size:18px inline.

• data-chart:
  слайд .slide--canvas; построй простую CSS-диаграмму в белой .card: столбики —
  div'ы с height по данным, ряд 1 var(--green), остальные пастель (#BFE9FF, #D2F5E4,
  #EFF5BF, #E8DEF8); подписи категорий снизу, значения над столбиками. Если данных
  для диаграммы нет — рецепт title-body.

• timeline (роадмап):
  слайд .slide--canvas; горизонтальная линия var(--stroke) с зелёными квадратными
  узлами; этапы = .rule-point под/над линией, равномерно по ширине.

• diagram-flow:
  слайд .slide--canvas; шаги = белые .card с padding 20px, между ними стрелки «→»
  (графит, 28px); 4+ шагов — две строки.

• team:
  слайд .slide--canvas; .grid --cols:3|4; карточка = .rule-point (h3 — имя,
  p — роль серым).

Любой архетип: на контентных слайдах ОБЯЗАТЕЛЬНЫ .slide__header + лого + копирайт.
На cover/section-divider — лого + копирайт (без .slide__header)."""

_FENCE_OPEN = re.compile(r"^```(?:html)?\s*")
_FENCE_CLOSE = re.compile(r"\s*```$")


def _strip_fences(html: str) -> str:
    html = html.strip()
    html = _FENCE_OPEN.sub("", html)
    html = _FENCE_CLOSE.sub("", html)
    return html.strip()


def build_messages(content: dict, brand_css: str) -> list[dict]:
    """System+user messages for one slide composition (reused by repair calls)."""
    user = (
        f"brand.css:\n```css\n{brand_css}\n```\n\n"
        f"Контент слайда (JSON):\n{json.dumps(content, ensure_ascii=False, indent=2)}\n\n"
        "Свёрстай слайд по рецепту своего архетипа. Верни только HTML-фрагмент."
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def compose_slide(content: dict, brand_css: str,
                  feedback: list[str] | None = None) -> str:
    """Author one slide body (HTML fragment) from a content payload.

    ``feedback`` (critic reasons / pixel-judge issues) is appended as an extra
    user turn so repair attempts converge instead of re-rolling blind.
    """
    messages = build_messages(content, brand_css)
    if feedback:
        messages.append({
            "role": "user",
            "content": ("Контроль качества нашёл дефекты. Перевёрстай слайд, исправив: "
                        + "; ".join(feedback)
                        + ". Верни только HTML-фрагмент."),
        })
    res = call_role(LLMCall(
        role=Role.SLIDE_COMPOSER,
        messages=messages,
        max_tokens_override=5000,
    ))
    return _strip_fences(res.content)
