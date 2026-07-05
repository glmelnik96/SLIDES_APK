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
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from webapp import draft
from webapp.paths import session_dir

_TEMPLATE_HINT = (
    "Выбор макета по смыслу: числа/метрики → stats-row/kpi; проценты → donut-chart; "
    "перечисление 2–6 пунктов → cards-6/grid-2x2/three-col; сравнение → two-col-cards; "
    "этапы → timeline; акцентная мысль → statement; титул → cover; контакты → contacts."
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
    action: str          # plan | add | rewrite | delete | move | retitle | chat
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
    "- chat: вопрос или реплика, не меняющая деку.\n"
    "Верни только JSON, без пояснений."
)


def classify(client: Any, message: str, *, ctx: str) -> Intent:
    messages = [
        {"role": "system", "content": _INTENT_SYSTEM},
        {"role": "user", "content": f"Контекст:\n{ctx}\n\nСообщение:\n{message}"},
    ]
    try:
        return client.chat_json(messages, Intent, max_tokens=256, retries=1,
                                extra_body={"thinking": {"type": "disabled"}})
    except Exception:  # noqa: BLE001 — on any parse failure, treat as plain chat
        return Intent(action="chat")


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
        lines.append(f"{i}. [{tid}] {title}".rstrip())
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
        library = TemplateLibrary.load()
        tid = _pick_template(client, library, intent.topic or message, ctx)
        spec = library.get(tid)
        # fill_slide понимает контекст: тема деки + бриф (что на слайде).
        sp = SlidePlan(index=len(plan.slides) + 1, type=spec.type,
                       template_id=tid,
                       content={"brief": intent.topic or message})
        try:
            sp = fill_slide(client, library, sp, deck_title=plan.title)
            new = draft.DraftSlide(template_id=tid, content=sp.content)
        except Exception:  # noqa: BLE001 — degrade to an empty slide, never crash
            new = draft.DraftSlide(template_id=tid, content={})
        plan = draft.add_slide(plan, new)
        draft.save_plan(session_id, plan)
        result = AgentResult(
            reply=f"Добавил слайд «{tid}» про: {intent.topic or 'тему'}.",
            changed=True, go_to=len(plan.slides))

    elif intent.action == "rewrite":
        if not (1 <= current_index <= len(plan.slides)):
            result = AgentResult(reply="Сначала выберите слайд для правки.")
        else:
            library = TemplateLibrary.load()
            cur = plan.slides[current_index - 1]
            tid = cur.template_id or "blank"
            spec = library.get(tid)
            sp = SlidePlan(index=current_index, type=spec.type, template_id=tid,
                           content={"brief": intent.topic or message,
                                    **(cur.content or {})})
            try:
                sp = fill_slide(client, library, sp, deck_title=plan.title,
                                extra=f"Указание пользователя: {intent.topic or message}")
                plan = draft.update_slide(plan, current_index, content=sp.content)
                draft.save_plan(session_id, plan)
                result = AgentResult(reply=f"Обновил слайд {current_index}.",
                                     changed=True, go_to=current_index)
            except Exception:  # noqa: BLE001
                result = AgentResult(
                    reply="Не получилось переписать слайд — попробуйте иначе.")

    elif intent.action == "plan":
        result = AgentResult(reply=_talk(client, _PLAN_SYSTEM, ctx, message))

    else:  # chat
        result = AgentResult(reply=_talk(client, _CHAT_SYSTEM, ctx, message))

    convo.turns.append(Turn(role="assistant", text=result.reply))
    save_chat(session_id, convo)
    return result


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
