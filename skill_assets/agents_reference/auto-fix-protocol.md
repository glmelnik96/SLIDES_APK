# Auto-Fix Protocol

**Назначение:** формализованная цепочка действий при нарушениях, обнаруженных Brand Guardian, Visual Validator, или LLM Visual Verifier. Заимствовано из Mck PPT Design Skill v2.3 (см. `memory/feedback_4_skills_lessons.md`).

**Когда вызывать:** автоматически после любого из:
- `brand_guardian.py` exit code 1 (WARN) или 2 (FAIL)
- `visual_validator.py` non-PASS
- `agent 10 LLM Visual Verifier` slide_verdict = NEEDS_REWORK
- Validate plan WARN с overflow flag

## Принцип

Не перепрыгивать сразу к «сменить donor» или «переделать вручную». Идти по цепочке от **самого щадящего фикса** к **самому радикальному**, останавливаясь как только проблема решена.

## Цепочка для overflow (текст не помещается)

### Шаг 1 — Remove redundancy
Удалить filler-фразы:
- «На самом деле», «как правило», «в общем»
- «Стоит отметить, что», «следует учитывать»
- Тавтологии: «эффективная эффективность», «внутри внутреннего»
- Дублирующие связки: «и при этом, и также»

**Стоп если** длина уменьшилась на ≥15% и помещается → переход к next iteration.

### Шаг 2 — Compress sentences
Переписать длинные фразы короче, не теряя смысла:
- «Cloud.ru является провайдером облачных услуг и AI-технологий» → «Cloud.ru — облака и AI»
- «Мы помогаем клиентам решать задачи через инфраструктуру и сервисы» → «Решаем бизнес-задачи через облако»
- Длинные перечисления → bullet-form: «X, Y, и Z» → «• X • Y • Z»

**Стоп если** ≤safe_max_chars donor.

### Шаг 3 — Restructure (split into bullets)
Если параграф >2 строк — split на пункты:
- Каждое предложение — отдельный bullet
- Bullets сокращаются до 4-7 слов
- Если на слайде 1+ длинный body → перевести в multicolumn donor (28/29/34)

**Стоп если** новый layout с теми же текстами помещается.

### Шаг 4 — Font micro-adjust
**Только если** шаги 1-3 не помогли. Уменьшить кегль на ≤25%:
- 88pt → 70pt (20%)
- 44pt → 36pt (18%)
- 32pt → 26pt (19%)
- 20pt → 16pt (20%)
- 14pt → **STOP** (не уменьшать body ниже 14pt — нечитаемо)

**Floor**: title ≥ 20pt, body ≥ 14pt. Если floor нарушен → шаг 5.

### Шаг 5 — Switch donor
Если шаги 1-4 не помогли, и не критично менять donor:
- Поискать в `donor-slot-map.yaml` другой donor той же `category` с **большим** `safe_max_chars`
- Например: `title_white_with_3d` (donor 5, max 30) → `title_white_compact` (donor 4, max 80)
- Layout Designer (агент 04) выбирает кандидата

### Шаг 6 — Split на 2 слайда (последний resort)
Если ничего не помогло — разбить контент:
- Title слишком длинный → title только тема + subtitle с подробностями + новый content слайд
- 6+ блоков → 2 слайда по 3 блока («1/2», «2/2»)
- KPI с 4+ цифрами → 2 KPI-слайда

**Никогда не делать стоп без решения.** Если все 6 шагов исчерпаны — флаг user'у с конкретным предложением.

## Цепочка для overlap (декор перекрывает текст)

### Шаг 1 — PNG-stripping
Если donor имеет `remove_before_fill: [shape_idx]` в slot-map — это PNG-fixed контент. Удалить эти shapes перед заполнением.

### Шаг 2 — Switch donor
Если `remove_before_fill` не помог или не указан — взять donor с пометкой `donor_type: pure_layout` (без декор-PNG).

### Шаг 3 — Reduce content
Сократить контент по цепочке overflow (шаги 1-3) — возможно проблема не в декоре, а в перенасыщенности.

## Цепочка для peer font inconsistency (Mck Peer Harmonize)

Если на одной Y-координате shapes имеют разные font sizes:

### Шаг 1 — Inspect intent
Проверить — это намеренная иерархия (title vs subtitle на той же Y) или случайность из-за donor-mix?
- Намеренная → пропустить, не фиксить
- Случайность (3 одинаковых KPI цифры разных размеров) → шаг 2

### Шаг 2 — Harmonize to min
Установить всем `min(sizes)` — самый маленький существующий размер. Это safer чем max (не вызовет overflow).

### Шаг 3 — Verify post-harmonize
После harmonize — проверить что не появилось новых overflow. Если появились → начать цепочку overflow.

## Цепочка для color off-palette

### Шаг 1 — Closest palette color
Если distance ≤ 50 RGB — заменить на ближайший палитровый цвет (Brand Guardian уже warns, не fail).

### Шаг 2 — Manual review
Если distance > 50 — это намеренный нестандартный цвет (например в импортированной графике). Флаг user'у с предложением: «оставить или заменить на Cloud.ru palette?»

## Цепочка для emoji

### Шаг 1 — Replace with brand icon
Эмодзи 📤 → линейная иллюстрация «стрелка» из Cloud.ru набора. Каждое emoji-семейство мапится на брендовую иконку.

### Шаг 2 — Strip
Если нет подходящей иконки — удалить эмодзи без замены. Текст должен читаться без него.

## Iteration limits

- Max **3 iterations** per slide через любую цепочку
- Если 3 итерации не решили — verdict NEEDS_REWORK с конкретным action для user

## Output format

Каждое применение auto-fix логируется:

```json
{
  "slide_num": 4,
  "trigger": "overflow_warning_from_validate",
  "chain_applied": "overflow",
  "steps_taken": [
    {"step": 1, "name": "remove_redundancy", "result": "no change", "delta_chars": -3},
    {"step": 2, "name": "compress_sentences", "result": "improved", "delta_chars": -22},
    {"step": 4, "name": "font_micro_adjust", "result": "fits", "before": "44pt", "after": "36pt"}
  ],
  "final_status": "RESOLVED",
  "iterations_used": 1
}
```

## Запреты

- ❌ НЕ перепрыгивать к шагу 4 (font reduce) минуя шаги 1-3
- ❌ НЕ использовать font reduce >25%
- ❌ НЕ опускать body ниже 14pt, title ниже 20pt
- ❌ НЕ split на слайды без попытки compress сначала
- ❌ НЕ менять palette без явного user approval (если distance > 50)

## Связи

- **Trigger sources:** `scripts/brand_guardian.py`, `scripts/visual_validator.py`, agent 10
- **Donor switching:** работает с агентом 04 Layout Designer
- **Cataлог donors:** `brand/donor-slot-map.yaml` (safe_max_chars, remove_before_fill)
- **Эстетический контекст:** `brand/DESIGN.md` (когда какой donor)
- **Overflow strategy memory:** `memory/feedback_overflow_strategy.md`
- **5-dim re-eval после fix:** agent 10 LLM Visual Verifier
