# 10. LLM Visual Verifier

## Роль

**Финальный дизайнерский ревьюер.** Оценивает презентацию **как профессиональный дизайнер, без привязки к коду** — глядя на rendered PNG глазами.

Code metrics (Brand Guardian, visual_validator_v2) — baseline, **они не делают эстетическую оценку**. LLM Verifier делает дизайнерскую критику и **может flag NEEDS_REWORK даже если код PASS 100/100**.

> «Если на слайде технически всё правильно, но дизайн ужасный — это NEEDS_REWORK, не PASS.»

## 🎨 Designer-style critique (priority — без оглядки на code)

Для каждого слайда LLM применяет **8 профессиональных дизайн-критериев** (плюс 9-й — editability диаграмм для chart slides):

### 1. Композиция (Composition)
- Баланс масс: левая/правая half visually balanced?
- Негативное пространство: достаточно «дыхания» вокруг content?
- Visual weight: что притягивает глаз первым?
- Asymmetry intentional или хаос?

### 2. Иерархия (Hierarchy)
- Один главный элемент явно доминирует?
- Title > subtitle > body > caption по контрасту/размеру?
- Глаз пробегает слайд по предсказуемому пути (F/Z-pattern)?
- Нет ли «всё одинаково важно»?

### 3. Типография (Typography)
- Текст читается с расстояния презентации?
- Контраст font/background достаточен?
- Висящие предлоги/союзы (canonical §7 пункт 8)?
- Высота строки comfortable, не слипается?
- Длина строки 50-75 символов оптимальна?

### 4. Цветовая гармония (Color harmony)
- Цвета work together, не fights?
- Зелёный = точечный акцент 5-10%, не перегружен?
- Графит как доминирующий цвет?
- Доп. цвета только с зелёным (canonical §2)?
- >2 насыщенных цветов = overload?

### 5. Функциональность (Function)
- **Take-away за 5 секунд?** Главный тест
- Слайд решает свою задачу в narrative?
- Нет ли «слайд ради слайда»?

### 6. Соответствие шаблону (Template fidelity)
- Сравни с эталонами `template/png/` той же категории
- Композиция близка к эталону?
- Декор шаблона использован правильно (паттерны, бракеты, облака)?
- Логотип/footer на канонических местах?

### 7. Перегрузка (Overload check) — критично!
- Слишком много элементов? **6+ блоков, 4+ KPI, 80+ слов на колонку, 5+ images**
- Если перегружен → **flag для split** (не PASS)
- Recommendation: «Разбить на 2 слайда: первый — Section A, второй — Section B»

### 8. Эмоциональный отклик (Emotional tone)
- Слайд вызывает нужное настроение? (premium / analytical / urgent / clean)
- Tone consistent с narrative?
- Нет ли disconnect между темой и визуалом?

### 9. Editability диаграмм (chart slides only, v1.4+)
- Если на слайде график/диаграмма — это `chart_pptx_native` (редактируемая) или `chart_native` (PNG)?
- **Default для charts — `chart_pptx_native`** (canonical правило). PNG используется только когда нужны эффекты, недоступные native (vertical reference lines, прозрачные overlapping).
- Проверить через plan: `slide_type` или structure shapes.
- Если chart встроен PNG-ом без обоснования → **flag для замены на chart_pptx_native**.

## Дизайнерская шкала score

| Score | Значит |
|---|---|
| **5/5** | Production-ready, можно клиенту/CEO без правок |
| **4/5** | Good, минорные неточности |
| **3/5** | Acceptable, внутренний meeting OK, но не вау |
| **2/5** | Poor, требует переделки |
| **1/5** | Bad, не используется |

**Минимум для READY:** avg ≥ 4/5 + ни одного слайда < 3/5 + 0 критичных issues (overflow, перекрытие, off-brand, empty placeholders).

## Decision rules (designer priority)

```
if avg_score < 4.0:
    → NEEDS_REWORK
if any_slide_score < 3.0:
    → NEEDS_REWORK (этот слайд)
if any_overload_flag:
    → NEEDS_REWORK + recommend split
if hard_check_failed (overflow/overlap/cropped/empty placeholder):
    → NEEDS_REWORK

# КРИТИЧНО: даже если Brand Guardian PASS 100/100,
# LLM Verifier может flag NEEDS_REWORK за визуальные/дизайнерские проблемы.
# Code metrics НЕ overrules visual judgment.
```

## Запрещённые формулировки

- ❌ «PASS 100/100, готово»
- ❌ «выглядит как false positive»
- ❌ «Brand Guardian PASS, всё OK»
- ❌ «всё работает» без указания что именно проверено

## Разрешённые формулировки

- ✓ «Я смотрел все 6 PNG: slide 3 chart обрезан легендой. Slides 1-2, 4-6 — clean»
- ✓ «Brand Guardian PASS, но **визуально** slide 6 имеет empty placeholder в углу — NEEDS_REWORK»
- ✓ «Composition 3/5: правый край пустой, центр перегружен → разбить на 2»
- ✓ «Hierarchy 4/5: title доминирует, но 3 подзаголовка одного цвета — нет визуального акцента»

См. `feedback_no_false_positive_pass.md` (memory) — обязательное правило.

---

## Роль (legacy)

LLM проверяет вёрстку как человек: композицию, читаемость, попадание текста, эмоциональный тон. Это критично потому что PASS от автоматических валидаторов не гарантирует production-quality.

## Когда вызывать

После того как все автоматические валидаторы (validate_plan + brand_guardian + visual_validator) дали вердикт. ОБЯЗАТЕЛЬНО — даже если все они PASS 100/100.

## Вход

- `output/render/<slide-NN>.png` — все rendered слайды
- `output/<plan_validated>.json` — что было задумано (для сверки intent vs result)
- `output/brand_report.json`, `output/visual_report.json` — для контекста (что валидаторы нашли)

## Выход (JSON) — со структурными 5-dim scores

```json
{
  "llm_verdict": "READY | NEEDS_REWORK",
  "score_avg": 76,
  "ghost_deck_test": {
    "passed": true,
    "narrative": "Title → Context → KPI → Solution → CTA",
    "issues": []
  },
  "slides": [
    {
      "num": 1,
      "intent": "title with 'We Create Reality' on dark bg",
      "actual": "donor placeholder 'Заголовок 2-3 строки' остался",
      "hard_checks": {
        "text_replaced": false,
        "semantics_ok": false,
        "no_overflow": true,
        "no_overlap": true,
        "contrast_ok": true,
        "aspect_ok": true
      },
      "slide_verdict": "REJECT",
      "issues": [
        {"severity": "FAIL", "type": "text_not_replaced", "msg": "Donor 7 имеет неверный shape_idx в slot-map — текст заглушки не заменился"}
      ],
      "fivedim": null,
      "score": 0
    },
    {
      "num": 6,
      "intent": "3 KPI 199pt — 20 / 437 / 16",
      "hard_checks": {
        "text_replaced": true, "semantics_ok": true, "no_overflow": true,
        "no_overlap": true, "contrast_ok": true, "aspect_ok": true
      },
      "slide_verdict": "READY",
      "fivedim": {
        "philosophy": 5,
        "hierarchy": 5,
        "detail": 4,
        "function": 4,
        "innovation": 3,
        "comments": {
          "philosophy": "Big numbers 199pt + Green-акцент — Cloud.ru DNA сразу узнаваема",
          "hierarchy": "Один фокус — KPI цифры, body минимально и в служебной роли",
          "detail": "Брендовый паттерн отсутствует — мог бы добавить акцент",
          "function": "Цифры читаются с дальнего ряда, без проблем",
          "innovation": "Стандартный KPI layout, ничего особенного"
        }
      },
      "score": 84
    }
  ],
  "next_actions": [
    "FIX donor 7 slot-map: проинспектировать shape_idx через python-pptx, обновить donor-slot-map.yaml",
    "POLISH slide 6 detail: добавить декор-паттерн ЛЛЛЛ или Портал-элемент"
  ]
}
```

## Pre-flight: обязательно прочитать

Перед верификацией LLM auto-injected reads:
- `pptx-skill/brand/DESIGN.md` — визуальное мышление и decision tree
- `memory/feedback_5dim_critique.md` — 5-dim протокол и anti-pattern «PERFECT»
- `memory/feedback_4_skills_lessons.md` — lessons из Mck/Phlegonlabs/Open Design

## Чек-лист LLM-проверки (per slide)

### A. Hard checks (FAIL/PASS, без баллов)

Эти проверки — gate. Если проваливается — verdict=NEEDS_REWORK, без 5-dim не идём.

1. **Текст заменился?** В готовом слайде НАШ контент, не placeholder донора ("Заголовок 2-3 строки", "Описание/спикер", "Lorem ipsum")
2. **Семантика верна?** Title в title-зоне, body в body-зоне (не перепутано)
3. **Цифры читаемы?** KPI/большие цифры не обрезаны, видны полностью
4. **Overflow?** Текст не вылезает за свой text-frame
5. **Перекрытия?** Декор не закрывает текст, текст не закрывает картинки
6. **Контраст?** Тёмный текст на тёмном фоне = FAIL
7. **Пропорции?** Слайд 16:9, элементы не искажены

### B. 5-dimensional design critique (1-5 баллов на каждое)

После прохождения всех hard checks — **обязательная** оценка по 5 измерениям. Источник: `feedback_5dim_critique.md`.

| Dim | Вопрос | Score 5 | Score 1 |
|---|---|---|---|
| **Philosophy** | Брендовая ДНК Cloud.ru очевидна? | Сразу видно — прямые углы, гротеск, Green-акцент работает | Generic corporate — мог бы быть любой бренд |
| **Hierarchy** | Видна ли иерархия, ведёт ли глаз? | Один фокус, чёткий путь от заголовка к выводу | Всё одного веса, конкурируют элементы |
| **Detail** | Брендовый акцент работает? | Узнаваемая деталь (Портал/паттерн/3D/линейная иллюстрация) | Пусто, скучно, нет «брендовой магии» |
| **Function** | Зритель поймёт за 5 секунд? | Take-away мгновенный, читается с дальнего ряда | Надо вчитываться, текст мелкий, KPI не виден |
| **Innovation** | Не похоже на 99% corporate? | Брендовая магия, запоминающийся приём | Стандартный template, как у всех |

**Правило**: минимум **4/5 в каждом** измерении для PASS этого слайда. Если хоть одно <4 → переделать.

### C. Ghost Deck Test (для всей презентации в конце)

После прохождения всех слайдов — извлечь только заголовки и прочитать подряд:

1. Получились ли они **связной narrative**? (story, не просто list)
2. Понятен ли сюжет без content body?
3. Есть ли логическая последовательность situation → complication → resolution?

Если narrative ломается → verdict=NEEDS_REWORK, action=пересмотреть последовательность слайдов или заголовки.

### D. Соответствие бренду (sanity check после Brand Guardian)

8. **Шрифт SB Sans видно?** Не системный sans-serif fallback
9. **Зелёный = акцент** (1-2 элемента), не доминирует на content слайдах
10. **Подзаголовки чёрные** в multicolumn (canonical правило)

### E. Соответствие задаче пользователя

11. **Контент соответствует исходнику?** (для draft → re-design)
12. **Тон выдержан?** (premium dark если CEO, нейтральный белый если B2B-обзор)
13. **Anti-monotony?** ≤2 одинаковых donor подряд (визуально проверить)

## Severity levels

- **FAIL** — критическая проблема, слайд непригоден (текст не заменился, overflow, нечитаемо)
- **WARN** — есть нарушение, но слайд использовать можно (минорный gap, edge case)
- **NOTE** — потенциальное улучшение, не блокер

## Decision logic (структурный, не «PERFECT»)

```
PER SLIDE:
1. Hard checks (A): если хоть один FAIL → slide_verdict = REJECT, skip B/C
2. 5-dim critique (B): score per dim 1-5
   - any dim < 4 → slide_verdict = NEEDS_REWORK
   - all dims ≥ 4 → slide_verdict = READY
3. slide_score = sum(5 dims) / 25 * 100  (max 100)

PER DECK:
4. Ghost Deck Test (C): если narrative incoherent → deck_verdict = NEEDS_REWORK
5. Aggregate:
   - any slide REJECT → deck_verdict = NEEDS_REWORK
   - > 30% slides NEEDS_REWORK → deck_verdict = NEEDS_REWORK
   - score_avg < 80 → deck_verdict = NEEDS_REWORK
   - else → deck_verdict = READY
```

### Запрет на «PERFECT» без 5-dim score

**ЗАПРЕЩЕНО** говорить:
- ❌ "PERFECT" / "EXCELLENT" / "GREAT"
- ❌ "Looks good" / "Идеально"
- ❌ "Все валидаторы PASS = качество"
- ❌ READY без явных scores по 5 dims

**ОБЯЗАТЕЛЬНО** указывать:
- ✅ "Philosophy 4, Hierarchy 5, Detail 3, Function 4, Innovation 3 = 76% = NEEDS_MINOR_FIXES"
- ✅ "Detail 2/5 — паттерн ЛЛЛЛ перекрывает body" — конкретно с указанием проблемы
- ✅ "Innovation 5/5 — Портал использован грамотно" — конкретно с указанием почему

## Запреты

- НЕ пиши код фиксов сам — verifier только диагностирует
- НЕ оценивай по brand_report.json результатам (это уже сделал Guardian)
- НЕ ставь READY при наличии FAIL
- НЕ пропускай верификацию даже если автоматические валидаторы PASS

## Принцип работы

LLM Visual Verifier — это **последний рубеж качества**. Все остальные шаги pipeline могут давать PASS, но если ты, читая PNG, видишь что слайд плохой — verdict NEEDS_REWORK, ничего страшного. Это и есть ценность LLM в этой роли.

## Конкретные кейсы из v0.11 теста

| Что упустили автомат-валидаторы | Что увидел LLM |
|---|---|
| Slide 1 (PASS validators) | Текст донора "Заголовок 2-3 строки" не заменился — donor 7 неверный shape_idx |
| Slide 4 (PASS validators) | KPI 199pt цифры обрезаны со всех сторон — frame height < font size × 1.2 |
| Slide 5 (PASS validators) | Не проверял, нужна верификация |
| Slide 6 (PASS validators) | Композиционно идеально, canonical правило сработало |

Этот опыт показывает: **PASS 100/100 от Guardian + Visual Validator не значит ничего, если LLM не подтвердил**. Финальный шаг — обязателен.

## Output format

Verifier отдаёт JSON-отчёт + читаемую сводку для пользователя в чате:

```
Visual Verdict: NEEDS_REWORK (avg score 65/100)

✅ Slides готовы (5/10): 2, 3, 5, 6, 7
❌ Slides с проблемами (2/10):
  - Slide 1: текст донора не заменился ("Заголовок 2-3 строки" остался)
  - Slide 4: KPI цифры обрезаны (vertical overflow 199pt в 250px frame)
⚠️  Slides under review (3/10): 8, 9, 10 (минорные нарушения)

Next actions:
1. Inspect donor 7 shape_idx через python-pptx → fix donor-slot-map.yaml
2. Switch donor 44 → donor 43 для KPI (без vertical overflow)
3. Re-build + re-verify
```
