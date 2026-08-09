"""Slide-building chat agent (feature 3): build a deck slide-by-slide in a chat.

The agent is a thin orchestration layer over the engine's atomic per-slide
operations. Each user turn is classified into an INTENT, then dispatched to an
action that mutates the session's DraftPlan and replies in natural language:

  plan      — discuss/propose structure (level 1: the assistant plans WITH you)
  add       — add a slide about X, picking a template by meaning + filling it
              with full context (level 2: understands the deck so far)
  rewrite   — restyle/shorten/format the text of the current slide
              (level 3: edits copy to fit the slide format)
  delete / move / retitle — structural ops on the plan
  chat      — answer/clarify without changing the deck

Context is everything: the deck title, a digest of existing slides, and the recent
chat history are passed into every model call, so the agent stays coherent (doesn't
repeat slides), picks layouts that fit the meaning, and edits copy to the format —
rather than working blind.

Conversation state lives in chat.json per session (history of turns). The DraftPlan
(plan.json) is the single source of truth for slides; deck.html is re-rendered from
it by the caller.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from webapp import draft, slide_types
from webapp.paths import session_dir

_TEMPLATE_HINT = (
    "Выбор макета по смыслу: числа/метрики → stats-row/kpi; проценты → donut-chart; "
    "перечисление 2–6 пунктов → cards-6/grid-2x2/three-col; сравнение → two-col-cards; "
    "этапы с датами → timeline; процесс с ветвлениями/цикл/воронка/оргсхема → diagram; "
    "акцентная мысль → statement; титул → cover; контакты → contacts."
)


# ── conversation store ───────────────────────────────────────────────────────
class Turn(BaseModel):
    role: str            # "user" | "assistant"
    text: str


class Conversation(BaseModel):
    turns: list[Turn] = Field(default_factory=list)


def _chat_path(session_id: str) -> Path:
    return session_dir(session_id) / "chat.json"


def load_chat(session_id: str) -> Conversation:
    p = _chat_path(session_id)
    if p.is_file():
        return Conversation.model_validate_json(p.read_text("utf-8"))
    return Conversation()


def save_chat(session_id: str, convo: Conversation) -> None:
    _chat_path(session_id).write_text(convo.model_dump_json(), encoding="utf-8")


# ── intent classification ────────────────────────────────────────────────────
class Intent(BaseModel):
    action: str          # plan | add | rewrite | delete | move | retitle | enrich | propose_content | build_now | chat
    topic: str = ""      # for add/rewrite: what the slide/edit is about
    to: int | None = None  # for move: target 1-based position


_INTENT_SYSTEM = (
    "Ты классификатор намерения для конструктора презентаций. По сообщению "
    "пользователя и контексту определи ОДНО действие и верни ТОЛЬКО JSON:\n"
    '{"action": "...", "topic": "...", "to": null}\n'
    "Действия:\n"
    "- plan: пользователь хочет обсудить/спланировать структуру всей презентации "
    "или просит предложить план.\n"
    "- add: добавить новый слайд (topic = о чём слайд).\n"
    "- rewrite: переписать/оформить/сократить текущий слайд (topic = что изменить).\n"
    "- delete: удалить текущий слайд.\n"
    "- move: переместить текущий слайд (to = новая позиция, 1-based).\n"
    "- retitle: задать заголовок всей презентации (topic = заголовок).\n"
    "- enrich: пользователь просит наполнить/детализировать/раскрыть существующие слайды плана контентом — обогатить их описания (brief). НЕ создаёт слайды.\n"
    "- propose_content: пользователь просит РАЗЛОЖИТЬ слайды по структурированным полям — предложить конкретный контент по типам (заголовок, тезисы-список, цифры, две колонки). Отличие от enrich: enrich дописывает текстовое описание, propose_content раскладывает слайд в типизированные поля. НЕ создаёт слайды.\n"
    "- build_now: пользователь ЯВНО просит запустить сборку/заполнение готового плана.\n"
    "- chat: вопрос или реплика, не меняющая деку.\n"
    "Верни только JSON, без пояснений."
)


# Консервативный rule-based фолбэк на случай, когда LLM-классификатор не вернул
# валидный JSON: раньше явное «удали слайд» молча превращалось в болтовню (chat),
# т.е. команда пользователя терялась без объяснений. Паттерны нарочно узкие —
# только однозначные формулировки; всё сомнительное по-прежнему уходит в chat.
_RULE_INTENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*(удали|убери)\b.{0,20}\bслайд", re.IGNORECASE), "delete"),
    (re.compile(r"^\s*добавь\b.{0,20}\bслайд", re.IGNORECASE), "add"),
    (re.compile(r"^\s*(предложи|составь|набросай)\b.{0,20}\bплан", re.IGNORECASE),
     "plan"),
    (re.compile(r"^\s*(собери|запусти)\b.{0,20}\b(деку|сборку|презентацию)",
                re.IGNORECASE), "build_now"),
]


def _rule_intent(message: str) -> Intent | None:
    for pattern, action in _RULE_INTENTS:
        if pattern.search(message):
            return Intent(action=action, topic=message.strip())
    return None


def classify(client: Any, message: str, *, ctx: str) -> Intent:
    messages = [
        {"role": "system", "content": _INTENT_SYSTEM},
        {"role": "user", "content": f"Контекст:\n{ctx}\n\nСообщение:\n{message}"},
    ]
    try:
        return client.chat_json(messages, Intent, max_tokens=256, retries=1,
                                extra_body={"thinking": {"type": "disabled"}})
    except Exception:  # noqa: BLE001 — on parse failure fall back to rules/chat
        return _rule_intent(message) or Intent(action="chat")


# ── context digest ───────────────────────────────────────────────────────────
def _slide_digest(plan: draft.DraftPlan) -> str:
    if not plan.slides:
        return "(слайдов пока нет)"
    lines = []
    for i, s in enumerate(plan.slides, start=1):
        tid = "freeform" if s.freeform else (s.template_id or "?")
        title = ""
        if isinstance(s.content, dict):
            title = str(s.content.get("title") or s.content.get("heading") or "")
        # Аутлайн: у незаполненного слайда заголовка ещё нет — покажем тему.
        label = title or (s.brief or "")
        lines.append(f"{i}. [{tid}] {label}".rstrip())
    return "\n".join(lines)


def context_brief(plan: draft.DraftPlan, convo: Conversation,
                  current_index: int) -> str:
    recent = convo.turns[-6:]
    hist = "\n".join(f"{t.role}: {t.text}" for t in recent)
    return (f"Заголовок деки: {plan.title or '(не задан)'}\n"
            f"Текущий слайд: {current_index}\n"
            f"Слайды:\n{_slide_digest(plan)}\n"
            f"Недавний диалог:\n{hist}")


# ── result of one agent turn ─────────────────────────────────────────────────
class AgentResult(BaseModel):
    reply: str                 # assistant message to show in chat
    changed: bool = False      # whether the deck/plan changed (UI should reload)
    go_to: int | None = None   # 1-based slide to focus after the change


# ── structured outline (level 1: plan the whole deck as a light outline) ─────
class OutlineItem(BaseModel):
    title: str = ""      # заголовок слайда (кладётся в content.title)
    brief: str = ""      # что на слайде — тема для последующей сборки


class OutlineDraft(BaseModel):
    slides: list[OutlineItem] = Field(default_factory=list)


# Потолок слайдов аутлайна (промпт просит 3-12; +запас на разумное превышение).
_OUTLINE_MAX_SLIDES = 15


class EnrichedItem(BaseModel):
    index: int          # 1-based, какой слайд плана
    brief: str = ""     # обновлённое описание


class EnrichedOutline(BaseModel):
    slides: list[EnrichedItem] = Field(default_factory=list)


class ProposedItem(BaseModel):
    index: int                 # 1-based слайд плана
    slide_type: str = ""       # title | bullets | stats | two_col
    fields: dict = Field(default_factory=dict)


class ProposedContent(BaseModel):
    slides: list[ProposedItem] = Field(default_factory=list)


_OUTLINE_SYSTEM = (
    "Ты — ассистент-конструктор презентаций Cloud.ru. По запросу пользователя "
    "составь структуру презентации — список слайдов. Верни ТОЛЬКО JSON вида "
    '{"slides": [{"title": "...", "brief": "..."}]}, где title — короткий '
    "заголовок слайда, brief — 1–2 предложения о том, что на слайде. Пиши "
    "по-русски, по-деловому. От 3 до 12 слайдов. Без пояснений вне JSON."
)


_ENRICH_SYSTEM = (
    "Ты дорабатываешь план презентации Cloud.ru. Тебе дан список слайдов "
    "(номер, заголовок, текущее описание) и запрос пользователя. Обнови "
    "ОПИСАНИЯ (brief) слайдов: добавь тезисы, факты, цифры, акценты. НЕ "
    "добавляй и НЕ удаляй слайды, не меняй их количество и порядок. "
    "Отвечай СРАЗУ одним JSON-объектом вида "
    '{"slides":[{"index":1,"brief":"..."}]}, по одному элементу на каждый '
    "изменённый слайд (index — номер из списка, 1-based; brief — новое "
    "описание). НЕ пиши никакого текста, рассуждений или Markdown до или "
    "после JSON. Без пояснений вне JSON."
)


_PROPOSE_SYSTEM = (
    "Ты раскладываешь слайды презентации Cloud.ru по СТРУКТУРИРОВАННЫМ полям. "
    "Тебе дан список слайдов (номер, заголовок, описание). Для каждого выбери "
    "ОДИН тип и заполни поля по его смыслу. Типы и поля:\n"
    "- title: обложка/титул. fields: {\"heading\": str, \"subtitle\": str}\n"
    "- bullets: список тезисов. fields: {\"heading\": str, \"bullets\": [str, ...]} (2–6 пунктов)\n"
    "- stats: цифры/метрики. fields: {\"heading\": str, \"stats\": [{\"value\": str, \"label\": str}, ...]} (2–4)\n"
    "- two_col: две колонки/сравнение. fields: {\"heading\": str, \"left\": [str, ...], \"right\": [str, ...]}\n"
    "Если слайд не ложится ни в один тип — пропусти его (не включай в ответ). "
    "heading обязателен у каждого. Отвечай СРАЗУ одним JSON-объектом вида "
    '{"slides":[{"index":1,"slide_type":"bullets","fields":{...}}]}. '
    "НЕ пиши текста, рассуждений или Markdown до или после JSON."
)


_PLAN_SYSTEM = (
    "Ты — ассистент-конструктор презентаций Cloud.ru. Помоги пользователю "
    "спланировать структуру деки: предложи список слайдов (по 1 строке: заголовок "
    "+ что на слайде), задавай уточняющие вопросы. Пиши кратко, по-деловому, "
    "по-русски. НЕ генерируй HTML — только обсуждение структуры."
)

_CHAT_SYSTEM = (
    "Ты — ассистент-конструктор презентаций Cloud.ru. Отвечай кратко и по делу "
    "на вопросы пользователя про его презентацию. По-русски."
)


def _kimi():
    from htmlslides.pipeline.client import KimiClient
    return KimiClient(timeout=280.0, max_retries=1,
                      extra_body={"thinking": {"type": "disabled"}})


def _talk(client: Any, system: str, ctx: str, message: str) -> str:
    reply = client.chat([
        {"role": "system", "content": system},
        {"role": "user", "content": f"Контекст:\n{ctx}\n\nСообщение:\n{message}"},
    ], max_tokens=900)
    return reply.strip() or "…"


def run_turn(session_id: str, message: str, current_index: int,
             *, client: Any | None = None) -> AgentResult:
    """Process one user turn: classify intent, act on the DraftPlan, reply.

    Pure-ish: mutates plan.json + chat.json on disk; the caller re-renders the
    deck. ``client`` is injectable for tests."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.pipeline.filler import fill_slide
    from htmlslides.models import SlidePlan

    client = client or _kimi()
    plan = draft.load_plan(session_id)
    convo = load_chat(session_id)
    convo.turns.append(Turn(role="user", text=message))
    ctx = context_brief(plan, convo, current_index)
    intent = classify(client, message, ctx=ctx)

    result: AgentResult
    if intent.action == "retitle":
        plan = plan.model_copy(update={"title": intent.topic or plan.title})
        draft.save_plan(session_id, plan)
        result = AgentResult(reply=f"Заголовок презентации: «{plan.title}».",
                             changed=True)

    elif intent.action == "delete":
        if 1 <= current_index <= len(plan.slides):
            plan = draft.delete_slide(plan, current_index)
            draft.save_plan(session_id, plan)
            result = AgentResult(reply=f"Удалил слайд {current_index}.",
                                 changed=True,
                                 go_to=max(1, current_index - 1))
        else:
            result = AgentResult(reply="Нет слайда для удаления.")

    elif intent.action == "move" and intent.to:
        try:
            plan = draft.reorder(plan, current_index, intent.to)
            draft.save_plan(session_id, plan)
            result = AgentResult(reply=f"Переместил слайд на позицию {intent.to}.",
                                 changed=True, go_to=intent.to)
        except IndexError:
            result = AgentResult(reply="Не получилось переместить — неверная позиция.")

    elif intent.action == "add":
        # Лёгкий аутлайн: только тема, БЕЗ синхронной сборки (fill_slide переехал
        # в build_outline). Слайд остаётся незаполненным до кнопки «Собрать деку».
        topic = intent.topic or message
        plan = draft.add_slide(
            plan, draft.DraftSlide(brief=topic, filled=False))
        draft.save_plan(session_id, plan)
        result = AgentResult(reply=f"Добавил в план: {topic}.",
                             changed=True, go_to=len(plan.slides))

    elif intent.action == "enrich":
        plan, result = _enrich_briefs(client, session_id, plan, ctx, message)

    elif intent.action == "propose_content":
        plan, result = _propose_content(client, session_id, plan, ctx, message)

    elif intent.action == "build_now":
        # Сборка — строго по кнопке в UI, чат её не запускает и не сигналит.
        result = AgentResult(
            reply="Когда план готов — нажми «Заполнить слайды» справа, чтобы собрать деку.",
            changed=False)

    elif intent.action == "rewrite":
        if not (1 <= current_index <= len(plan.slides)):
            result = AgentResult(reply="Сначала выберите слайд для правки.")
        else:
            library = TemplateLibrary.load()
            cur = plan.slides[current_index - 1]
            tid = cur.template_id or "blank"
            base = cur.content or {}
            # У typed-слайда (схема) истина — в fields, а content остаётся с
            # момента последнего заполнения: панель узлов пишет только fields.
            # Без пересчёта в чат уезжала ПРЕЖНЯЯ схема, и просьба «упрости»
            # молча откатывала всё, что автор наменял руками на слайде.
            if cur.slide_type and cur.fields:
                typed_tid, mapped = slide_types.map_typed(cur.slide_type,
                                                          cur.fields)
                if typed_tid:
                    tid, base = typed_tid, mapped
            spec = library.get(tid)
            sp = SlidePlan(index=current_index, type=spec.type, template_id=tid,
                           content={"brief": intent.topic or message, **base})
            try:
                sp = fill_slide(client, library, sp, deck_title=plan.title,
                                extra=f"Указание пользователя: {intent.topic or message}")
                plan = draft.update_slide(plan, current_index,
                                          content=sp.content, template_id=tid)
                # Присваиваем ВСЕГДА: typed-поля старше template_id (draft_render
                # рисует слайд из них), поэтому уцелевшие поля прежней схемы
                # показывали бы её вместо только что переписанного содержимого.
                typed = slide_types.typed_from_content(tid, sp.content)
                cur_slide = plan.slides[current_index - 1]
                cur_slide.slide_type, cur_slide.fields = typed or (None, None)
                draft.save_plan(session_id, plan)
                result = AgentResult(reply=f"Обновил слайд {current_index}.",
                                     changed=True, go_to=current_index)
            except Exception:  # noqa: BLE001
                result = AgentResult(
                    reply="Не получилось переписать слайд — попробуйте иначе.")

    elif intent.action == "plan":
        plan, result = _generate_outline(client, session_id, plan, ctx, message)

    else:  # chat
        result = AgentResult(reply=_talk(client, _CHAT_SYSTEM, ctx, message))

    convo.turns.append(Turn(role="assistant", text=result.reply))
    save_chat(session_id, convo)
    return result


def _generate_outline(client: Any, session_id: str, plan: draft.DraftPlan,
                      ctx: str, message: str) -> tuple[draft.DraftPlan, AgentResult]:
    """Ядро П2: сгенерировать структурный аутлайн и дописать его как
    незаполненные слайды (заголовок → content.title, тема → brief). При сбое
    генерации — откат на текстовое обсуждение. Возвращает (plan, result)."""
    try:
        outline = client.chat_json(
            [{"role": "system", "content": _OUTLINE_SYSTEM},
             {"role": "user",
              "content": f"Контекст:\n{ctx}\n\nЗапрос:\n{message}"}],
            OutlineDraft, max_tokens=1500, retries=1,
            extra_body={"thinking": {"type": "disabled"}})
    except Exception:  # noqa: BLE001
        outline = None
    if outline and outline.slides:
        # Промпт просит 3-12 слайдов, но JSON модели никто не ограничивал —
        # разошедшаяся модель могла вывалить десятки слайдов в план. Жёсткий cap.
        outline.slides = outline.slides[:_OUTLINE_MAX_SLIDES]
        for item in outline.slides:
            plan = draft.add_slide(plan, draft.DraftSlide(
                brief=item.brief or item.title, filled=False,
                content={"title": item.title} if item.title else {}))
        draft.save_plan(session_id, plan)
        lines = "\n".join(f"{i}. {it.title or it.brief}".rstrip()
                          for i, it in enumerate(outline.slides, start=1))
        return plan, AgentResult(
            reply=f"Набросал план ({len(outline.slides)} сл.):\n{lines}",
            changed=True, go_to=len(plan.slides))
    return plan, AgentResult(
        reply=_talk(client, _PLAN_SYSTEM, ctx, message), changed=False)


def _enrich_briefs(client: Any, session_id: str, plan: draft.DraftPlan,
                   ctx: str, message: str) -> tuple[draft.DraftPlan, AgentResult]:
    """Обогатить brief'ы существующих незаполненных слайдов аутлайна на месте
    по контексту обсуждения. Слайды не добавляются/не удаляются. (plan, result)."""
    # Цели — только незаполненные обычные слайды (готовые/freeform не трогаем).
    targets = [i for i, s in enumerate(plan.slides, start=1)
               if s.brief and not s.filled and not s.freeform]
    if not targets:
        return plan, AgentResult(
            reply="В плане пока нет слайдов для доработки — сначала набросаем структуру.",
            changed=False)
    lines = "\n".join(
        f"{i}. {plan.slides[i - 1].content.get('title', '')} — {plan.slides[i - 1].brief}"
        for i in targets)
    user = (f"Контекст:\n{ctx}\n\nАутлайн:\n{lines}\n\nЗапрос:\n{message}")
    try:
        outline = client.chat_json(
            [{"role": "system", "content": _ENRICH_SYSTEM},
             {"role": "user", "content": user}],
            EnrichedOutline, max_tokens=4000, retries=2,
            extra_body={"thinking": {"type": "disabled"}})
    except Exception:  # noqa: BLE001
        outline = None
    if outline and outline.slides:
        applied = 0
        for item in outline.slides:
            if item.index in targets:
                plan.slides[item.index - 1].brief = item.brief
                applied += 1
        draft.save_plan(session_id, plan)
        return plan, AgentResult(
            reply=f"Дополнил план деталями ({applied} сл.).", changed=True)
    return plan, AgentResult(
        reply=_talk(client, _PLAN_SYSTEM, ctx, message), changed=False)


def _propose_content(client: Any, session_id: str, plan: draft.DraftPlan,
                     ctx: str, message: str) -> tuple[draft.DraftPlan, AgentResult]:
    """Разложить сырые слайды аутлайна по типизированным полям. Невалидные
    предложения игнорируются (слайд остаётся сырым → fallback на сборке).
    Слайды не добавляются/не удаляются. Возвращает (plan, result)."""
    targets = [i for i, s in enumerate(plan.slides, start=1)
               if s.brief and not s.filled and not s.freeform and not s.slide_type]
    if not targets:
        return plan, AgentResult(
            reply="Нет сырых слайдов для раскладки — сначала набросаем план.",
            changed=False)
    lines = "\n".join(
        f"{i}. {plan.slides[i - 1].content.get('title', '')} — {plan.slides[i - 1].brief}"
        for i in targets)
    user = f"Контекст:\n{ctx}\n\nСлайды:\n{lines}\n\nЗапрос:\n{message}"
    try:
        proposed = client.chat_json(
            [{"role": "system", "content": _PROPOSE_SYSTEM},
             {"role": "user", "content": user}],
            ProposedContent, max_tokens=4000, retries=2,
            extra_body={"thinking": {"type": "disabled"}})
    except Exception:  # noqa: BLE001
        proposed = None
    applied = 0
    if proposed and proposed.slides:
        for item in proposed.slides:
            if item.index not in targets:
                continue
            norm = slide_types.validate_fields(item.slide_type, item.fields)
            if norm is None:
                continue  # не ложится в тип → слайд остаётся сырым (fallback)
            plan.slides[item.index - 1] = plan.slides[item.index - 1].model_copy(
                update={"slide_type": item.slide_type, "fields": norm,
                        "filled": False})
            applied += 1
    if applied:
        draft.save_plan(session_id, plan)
        return plan, AgentResult(
            reply=(f"Разложил по полям {applied} сл. Проверь и поправь в "
                   "аутлайне — потом жми «Собрать»."), changed=True)
    # Ничего не разложилось. Самая частая причина — слишком общие brief'ы
    # (модели не из чего лепить поля). Подскажем обогатить план сначала.
    thin = all(len(plan.slides[i - 1].brief) < 40 for i in targets)
    if thin:
        reply = ("Слайды слишком общие, чтобы разложить по полям. Сначала "
                 "«дополни план деталями» — добавь тезисы, цифры, факты, — "
                 "потом снова жми «Предложить контент».")
    else:
        reply = "Не получилось разложить слайды по полям — попробуй иначе."
    return plan, AgentResult(reply=reply, changed=False)


def build_outline(session_id: str, *, client: Any | None = None) -> None:
    """Fill the whole outline: run every un-filled outline slide through
    ``fill_slide`` and re-render the deck. Saves after each slide (resilient to
    a mid-build crash) and never lets one bad slide abort the run."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.pipeline.filler import fill_slide
    from htmlslides.models import SlidePlan
    from webapp import draft_render  # inside to avoid an import cycle

    client = client or _kimi()
    library = TemplateLibrary.load()
    plan = draft.load_plan(session_id)
    convo = load_chat(session_id)
    for i, s in enumerate(plan.slides, start=1):
        if not (s.brief and not s.filled and not s.freeform and not s.slide_type):
            continue
        ctx = context_brief(plan, convo, i)
        tid = _pick_template(client, library, s.brief, ctx)
        spec = library.get(tid)
        sp = SlidePlan(index=i, type=spec.type, template_id=tid,
                       content={"brief": s.brief})
        try:
            sp = fill_slide(client, library, sp, deck_title=plan.title)
            plan = draft.update_slide(plan, i, content=sp.content,
                                      template_id=tid)
        except Exception:  # noqa: BLE001 — keep template only, never crash
            plan = draft.update_slide(plan, i, template_id=tid)
        # Вопрос стеклянной сборки снимаем: слайд уже заполнен здесь, и карточка
        # «ИИ сомневается в макете» относилась бы к содержимому, которого нет.
        # Черновик у обоих путей общий — автор может уйти из степпера в чат.
        slide = plan.slides[i - 1]
        # Схему — в typed-слайд (панель узлов и drag смотрят на slide_type).
        typed = slide_types.typed_from_content(tid, slide.content)
        if typed:
            slide.slide_type, slide.fields = typed
        slide.filled = True
        slide.status = slide.question = slide.candidates = None
        draft.save_plan(session_id, plan)  # save after EACH slide (resilience)
    draft_render.render_draft(session_id, plan)  # final render


def _pick_template(client: Any, library, topic: str, ctx: str) -> str:
    """Ask the model to choose a template id by meaning; fall back to 'blank'."""
    ids = [t.id for t in library.templates
           if t.id not in ("section-dots", "section-frame", "back-cover")]
    system = ("Выбери ОДИН id макета слайда под смысл. " + _TEMPLATE_HINT +
              "\nВерни ТОЛЬКО id из списка: " + ", ".join(ids))
    try:
        # Бюджет не 24, а 256: MiniMax-M3 всегда эмитит короткий reasoning (~100
        # токенов) ПЕРЕД content, и на 24/64 токенах content выходит пустым →
        # выбор макета всегда падал в "blank". Порог по замеру — между 64 и 128.
        reply = client.chat([
            {"role": "system", "content": system},
            {"role": "user", "content": f"Контекст:\n{ctx}\n\nСлайд про: {topic}"},
        ], max_tokens=256).strip()
        for tid in ids:
            if tid in reply:
                return tid
    except Exception:  # noqa: BLE001
        pass
    return "blank"
