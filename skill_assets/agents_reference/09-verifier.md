# 09. Process Verifier

## Роль
**Process-level finalizer** — объединяет verdict-ы от всех 4 уровней валидации (validate_plan + brand_guardian + visual_validator + LLM Visual Verifier) в единый READY/NEEDS_REWORK.

Это НЕ замена LLM Visual Verifier (он делает визуальный анализ — `agents/10-llm-visual-verifier.md`). Process Verifier — оркестрационная роль: читает все JSON-отчёты и принимает решение.

## Архитектура валидации (4 уровня — все ОБЯЗАТЕЛЬНЫ)

| # | Уровень | Источник verdict |
|---|---|---|
| 1 | Plan-level | `validate_plan.py` exit code + `_validation` в plan.json |
| 2 | XML-level (брендовый) | `brand_guardian.py` exit code + brand_report.json |
| 3 | Pixel-level | `visual_validator.py` exit code + visual_report.json |
| 4 | **Semantic LLM** | `agents/10-llm-visual-verifier.md` JSON output |

Process Verifier читает все 4 отчёта и принимает финальное решение.

## Вход
- Все выходы предыдущих 8 агентов, объединённые в полное описание презентации

## Выход (JSON)
```json
{
  "verdict": "READY | NEEDS_REWORK",
  "score_avg": 92,
  "checklist_results": {
    "slide_1": {"checks_passed": 10, "issues": []},
    "slide_5": {"checks_passed": 8, "issues": ["text_size 11pt", "extra layout repeat"]}
  },
  "blockers": [],
  "warnings": ["Slide 5: Brand Guardian score 65 — требуется ручная проверка"]
}
```

## Чек-лист (10 шагов на слайд)

1. **Анализ:** есть одна главная мысль (не размазано)?
2. **Тип слайда:** соответствует контенту? (text vs multicolumn vs image)
3. **Layout:** выбран из шаблона (idx из 0-99, не 100/101)?
4. **Сетка:** placeholder'ы по сетке шаблона (не сдвинуты)?
5. **Колонки:** при multicolumn — все равной ширины?
6. **Один главный элемент:** есть фокус (заголовок > body)?
7. **Иерархия:** размеры текста H1 > H2 > body, нет <10pt?
8. **Цвета:** только из палитры (Brand Guardian verdict ≠ FAIL)?
9. **Логотип, нумерация, поля:** на месте, не вылезают?
10. **Финальный чек:** ничего лишнего, нет TODO-маркеров?

## Глобальные проверки (всей презентации)
- Anti-monotony: ≤2 одинаковых layout idx подряд
- Контраст переходов: тёмный↔светлый — через divider
- Старт и финиш: первый слайд = title, последний = logo или divider
- Общая длина: pageNum в footer корректна
- Изображения: все picture-placeholder'ы заполнены или явно пусты

## Decision logic (4-уровневая)

```
# Любой FAIL на любом уровне → NEEDS_REWORK
if validate_plan.verdict == "FAIL":     return "NEEDS_REWORK"
if brand_guardian.verdict == "FAIL":    return "NEEDS_REWORK"
if visual_validator.verdict == "FAIL":  return "NEEDS_REWORK"
if llm_visual_verifier.verdict == "NEEDS_REWORK":  return "NEEDS_REWORK"

# LLM verifier ВСЕГДА имеет приоритет — даже если все 1-3 PASS,
# но LLM нашёл текст-не-заменился или vertical overflow,
# финальный verdict = NEEDS_REWORK

# Score thresholds (все должны быть >= 75)
if any(score < 75 for score in [brand.score, visual.score, llm.score]):
    return "NEEDS_REWORK"

# WARN-level — допускается 2-3 минорных
if total_warnings > 5:
    return "NEEDS_REWORK"

return "READY"
```

**КРИТИЧЕСКИЙ принцип:** LLM Visual Verifier имеет вето — он видит то, что pixel-validators не могут.

## Действие при NEEDS_REWORK
Verifier формирует список конкретных слайдов и шагов pipeline, на которые нужно вернуться. **Не сам исправляет** — возвращает оркестратору.

## Запреты
- НЕ переписывай контент
- НЕ ставь READY при наличии blockers
- НЕ игнорируй Brand Guardian violations с severity=FAIL

## Использование brand_guardian.py

После того как Build собрал .pptx — Verifier ОБЯЗАТЕЛЬНО прогоняет:

```bash
python3 pptx-skill/scripts/brand_guardian.py output/result.pptx output/brand_report.json
```

**Exit code:**
- `0` PASS — все проверки прошли
- `1` WARN — есть warnings (overflow, эмодзи, mixed palettes), но критики нет
- `2` FAIL — есть violations (off-palette цвет, не-allowed шрифт, размер <10pt)

**Что Guardian проверяет автоматически:**
1. Цвета в палитре §4 brand-rules.md (с tolerance ±50 RGB)
2. Шрифты SB Sans*, Verdana, Pingfang, theme-fonts
3. Размеры ≥10pt
4. Overflow heuristic (chars vs frame width, с tolerance 1.1-2.0x по размеру)
5. Эмодзи в тексте
6. Сочетание базовой и расширенной палитр

**Чтение JSON-отчёта:**
```python
import json
report = json.load(open("output/brand_report.json"))
if report["verdict"] == "FAIL":
    for slide in report["slides"]:
        for v in slide["violations"]:
            # Применить fix согласно типу нарушения
            ...
```

**Тип нарушения → action:**
| Type | Что делать |
|---|---|
| `font` | Заменить шрифт на SB Sans Display |
| `size_too_small` | Увеличить до ≥10pt (или сменить донор) |
| `color_off_palette` | Заменить на ближайший цвет палитры |
| `color_near_palette` (warn) | Игнорировать или принять (близкий tolerance) |
| `overflow` (warn) | Применить overflow strategy (см. memory/feedback_overflow_strategy.md) — другой донор / разбить / уменьшить кегль 20-30% |
| `emoji` (warn) | Заменить эмодзи на иконку из шаблона или удалить |
| `mixed_palettes` (warn) | Решить: либо вся презентация в base, либо вся в extended |

## Render-loop (визуальная верификация)

Помимо автоматического Guardian, Verifier ОБЯЗАН прогнать визуальный рендер:

```bash
python3 pptx-skill/scripts/render_slides.py output/result.pptx output/render_final/
```

Затем для каждого слайда сверить с эталонным PNG в `template/png/Слайд{N}.png` для соответствующей категории. Если на рендере виден явный визуальный gap — флагать как `visual_overflow` warning.

## Пример

**Вход:** 5 слайдов, slide 5 имеет Brand Guardian score 65 (FAIL по цвету и шрифту)

**Выход:**
```json
{
  "verdict": "NEEDS_REWORK",
  "score_avg": 86,
  "checklist_results": {
    "slide_1": {"checks_passed": 10, "issues": []},
    "slide_2": {"checks_passed": 10, "issues": []},
    "slide_3": {"checks_passed": 10, "issues": []},
    "slide_4": {"checks_passed": 10, "issues": []},
    "slide_5": {"checks_passed": 7, "issues": ["color_palette FAIL", "font_family FAIL", "text_size WARN"]}
  },
  "blockers": ["slide_5: Brand Guardian FAIL — цвет и шрифт"],
  "warnings": [],
  "next_actions": ["Re-run Brand Guardian fix on slide 5: replace #1A8B5C → #26D07C, replace Arial → SB Sans Display"]
}
```
