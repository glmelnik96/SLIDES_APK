"""Роль 2c — Diagram Filler: строгий DiagramSpec по брифу раздела.

Отличие от слот-филла: ответ модели — не текст по контракту слотов, а СТРУКТУРА
(узлы/связи), которую рисует детерминированный движок diagram.js. Валидация
двухслойная (schema.parse_diagram: Pydantic + семантика) возвращает СПИСОК
претензий — ретраим один раз с полным перечнем, дальше DiagramFillError
(filler переводит его в FillError → фолбэк на blank, дека не падает).

Каталог доступных типов и «когда брать» приходит из diagrams.catalog — новый
реализованный тип попадает в промпт автоматически.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ..diagrams.catalog import CATALOG
from ..diagrams.schema import (MAX_CYCLE, MAX_DURATION, MAX_EDGE_LABEL,
                               MAX_EDGES, MAX_GANTT_ROWS, MAX_ID, MAX_LABEL,
                               MAX_LANE, MAX_NODES, MAX_STEPS, MAX_VALUE,
                               DiagramValidationError, parse_diagram)
from ..library import TemplateLibrary
from ..models import SlidePlan
from .client import KimiClient

# Синхронно с filler._FILL_MAX_TOKENS/_FILL_NO_THINK (импорт оттуда — цикл:
# filler импортирует нас). JSON диаграммы крупнее слот-филла, но в 8192 влезает
# с запасом; глубокое рассуждение структуре из 12 узлов не нужно.
_FILL_MAX_TOKENS = 8192
_FILL_NO_THINK = {"thinking": {"type": "disabled"}}


class DiagramFillError(RuntimeError):
    """Диаграмма не прошла контракт после ретрая."""


class DiagramSlideReply(BaseModel):
    title: str = ""
    subtitle: str = ""
    diagram: dict = Field(default_factory=dict)


def _kinds_menu() -> str:
    return "\n".join(f"- {t.kind}: {t.when_to_use}"
                     for t in CATALOG if t.available)


_EXAMPLE = json.dumps({
    "title": "Как проходит заявка",
    "subtitle": "От обращения до подключения услуги",
    "diagram": {
        "kind": "flowchart", "direction": "right",
        "nodes": [
            {"id": "start", "label": "Заявка клиента", "shape": "start"},
            {"id": "check", "label": "Проверка данных", "shape": "process"},
            {"id": "ok", "label": "Данные полные?", "shape": "decision"},
            {"id": "done", "label": "Услуга подключена", "shape": "end",
             "accent": True},
        ],
        "edges": [
            {"from": "start", "to": "check"},
            {"from": "check", "to": "ok"},
            {"from": "ok", "to": "done", "label": "да"},
            {"from": "ok", "to": "check", "label": "нет"},
        ],
    },
}, ensure_ascii=False)

DIAGRAM_SYSTEM = f"""\
Ты строишь СТРУКТУРУ схемы для слайда презентации Cloud.ru: узлы и связи,
которые нарисует детерминированный движок. Верни ТОЛЬКО JSON вида
{{"title": "...", "subtitle": "...", "diagram": {{...}}}} — без текста вокруг.

Типы диаграмм (kind) — выбери ОДИН по смыслу раздела:
{_kinds_menu()}

Контракт diagram:
- kind — строго из списка выше; узлов 2–{MAX_NODES}, рёбер ≤{MAX_EDGES}.
- nodes: [{{"id","label","shape","accent","value","lane","level"}}] — других
  ключей у узла НЕ бывает; id — короткая латиница ≤{MAX_ID} символов (n1,
  check…); label обязателен и непустой, ≤{MAX_LABEL} символов, по-русски, без
  выдумок; accent:true — у 1–2 ключевых узлов; value — только для
  funnel/pyramid (число слоя, напр. "1200"), gantt_lite (длительность в
  периодах) и steps (пометка ступени, напр. "Уровень 2"), ≤{MAX_VALUE}
  символов; lane — только для comparison/swimlanes, ≤{MAX_LANE} символов;
  level — только для gantt_lite (стартовый период, целое от 0).
- shape — только для flowchart: start|end|process|decision|io; у decision
  подписывай исходящие рёбра («да»/«нет»).
- edges: [{{"from","to","label"}}] — других ключей нет, label ≤{MAX_EDGE_LABEL}
  символов; только ссылки на существующие id, без петель.
  Для process/cycle/funnel/pyramid/matrix/venn/steps/gantt_lite рёбра НЕ нужны
  (порядок задаёт список узлов); для hierarchy ребро = родитель→ребёнок, у узла
  один родитель.
- direction: "right" (по умолчанию) или "down" — для flowchart.
- Правила отдельных типов:
  matrix — ровно 4 узла в порядке квадрантов (верх-лево, верх-право, низ-лево,
  низ-право); подписи осей — meta: {{"x_axis","y_axis"}}.
  cycle — 3–{MAX_CYCLE} стадий по кругу, стрелки идут по порядку списка.
  pyramid — узлы сверху вниз, вершина первой (3+ уровней).
  hub_spoke — ПЕРВЫЙ узел = центр, остальные — лучи; рёбра не нужны.
  comparison — у каждого узла lane = название одной из ДВУХ сторон.
  swimlanes — у каждого узла lane = исполнитель шага (2–5 дорожек); рёбра
  задают порядок шагов.
  venn — 2–3 узла-множества; подпись пересечения — meta: {{"center_label"}}.
  gantt_lite — 2–{MAX_GANTT_ROWS} работ сверху вниз; value обязателен (длительность,
  целое 1–{MAX_DURATION}), level — номер стартового периода (без него работа встаёт
  сразу за предыдущей); название единицы шкалы — meta: {{"x_axis"}}, напр. «Месяцы».
  steps — 2–{MAX_STEPS} ступеней от нижней к верхней; рёбра не нужны.
  mindmap — ПЕРВЫЙ узел = центр; ребро = ветвь→подветвь, у подветви один
  родитель, в центр рёбра НЕ ведут. Либо рёбер нет вовсе (плоская карта: все
  узлы — ветви центра), либо КАЖДЫЙ узел, кроме центра, имеет входящее ребро,
  включая ветви первого уровня (ребро из центра). Смешивать нельзя.
  network — произвольная сеть, раскладку задают рёбра: минимум 3 узла и хотя бы
  одна связь, узлов без связей не оставляй.
- НЕ добавляй offsets, groups, version и любые не названные здесь ключи —
  раскладку считает движок; meta — только там, где разрешено выше.
- title ≤54 симв. — заголовок ЭТОГО слайда (из брифа), не всей деки;
  subtitle ≤70 симв., можно пустой.

Пример ответа:
{_EXAMPLE}

Пиши по-русски, кратко и фактично: только сущности и связи из брифа."""


def fill_diagram(client: KimiClient, library: TemplateLibrary,
                 slide: SlidePlan, *, deck_title: str = "",
                 extra: str = "", kind: str = "") -> SlidePlan:
    """Заполнить диаграммный слайд. Контент слайда: title/subtitle + канонический
    JSON DiagramSpec в слоте diagram (строка — по слот-контракту шаблона).

    ``kind`` — тип схемы, выбранный ЧЕЛОВЕКОМ (мастер «Схема»). Это не подсказка,
    а требование: несовпадение идёт в тот же список претензий, что и поломка
    контракта, то есть чинится ретраем. Без этого выбор автора («воронка») уходил
    в модель просто текстом, и она спокойно рисовала блок-схему — «выбираешь один
    макет, применяется другой»."""
    # Детерминированно готовый слайд (пикер редактора уже положил валидный спек)
    # не гоняем через LLM — симметрично скип-пути _fill_template.
    if not extra and "brief" not in slide.content and not _content_errors(
            library, slide.content, kind=kind):
        return slide
    user = f"Заголовок деки: {deck_title}\n\nБриф слайда:\n{_brief(slide)}"
    if kind:
        user += (f'\n\nТип схемы выбрал автор: {kind}. Поле "kind" в JSON '
                 f'должно быть ровно "{kind}" — уложи содержание раздела в '
                 "этот тип, другой не предлагай.")
    if extra:
        user += "\n\n" + extra
    messages = [{"role": "system", "content": DIAGRAM_SYSTEM},
                {"role": "user", "content": user}]
    reply = client.chat_json(messages, DiagramSlideReply,
                             max_tokens=_FILL_MAX_TOKENS,
                             extra_body=_FILL_NO_THINK)
    content, errors = _to_content(library, reply, kind=kind)
    if errors:
        listed = "; ".join(errors)
        retry = messages + [
            {"role": "assistant",
             "content": reply.model_dump_json()},
            {"role": "user",
             "content": f"Схема не прошла контракт: {listed}. Исправь все "
                        "претензии и верни ТОЛЬКО валидный JSON того же вида."}]
        reply = client.chat_json(retry, DiagramSlideReply,
                                 max_tokens=_FILL_MAX_TOKENS,
                                 extra_body=_FILL_NO_THINK)
        content, errors = _to_content(library, reply, kind=kind)
        if errors:
            # Без префикса "slide N" — его добавит fill_slide при переводе
            # в FillError (иначе номер задваивался в warn-прогрессе).
            raise DiagramFillError("диаграмма не прошла контракт после "
                                   f"ретрая: {'; '.join(errors)}")
    content["diagram"] = _carry_offsets(slide.content.get("diagram"),
                                        content["diagram"])
    return slide.model_copy(update={"content": content})


def _carry_offsets(old_raw, new_raw: str) -> str:
    """Вернуть новой схеме ручные сдвиги узлов из прежней (по id).

    Модели запрещено присылать offsets — раскладку считает движок. Но сдвиги
    ставит РУКА: человек растащил узлы на слайде, а потом нажал «Проверить и
    улучшить слайды» (или попросил правку в чате) — и схема молча возвращалась
    к автораскладке, потому что перезаполнение отдаёт спек без offsets. Узлы,
    которых в новой схеме не осталось, отсеет сама схема при разборе."""
    if not isinstance(old_raw, str) or not old_raw or not new_raw:
        return new_raw
    try:
        offsets = json.loads(old_raw).get("offsets")
        if not offsets:
            return new_raw
        spec = json.loads(new_raw)
        spec["offsets"] = offsets
        return json.dumps(parse_diagram(spec).model_dump(by_alias=True),
                          ensure_ascii=False, separators=(",", ":"))
    except (ValueError, AttributeError, DiagramValidationError):
        return new_raw       # прежний спек не читается — новая схема как есть


def _brief(slide: SlidePlan) -> str:
    return str(slide.content.get("brief", "")) or json.dumps(
        slide.content, ensure_ascii=False)


def _to_content(library: TemplateLibrary, reply: DiagramSlideReply, *,
                kind: str = "") -> tuple[dict, list[str]]:
    """Ответ модели → контент слайда + список претензий (пустой = валидно)."""
    errors: list[str] = []
    diagram_raw = ""
    try:
        spec = parse_diagram(reply.diagram)
        if kind and spec.kind != kind:
            errors.append(f'kind: автор выбрал тип "{kind}", а в схеме '
                          f'"{spec.kind}" — перестрой схему в "{kind}"')
        diagram_raw = json.dumps(spec.model_dump(by_alias=True),
                                 ensure_ascii=False, separators=(",", ":"))
    except DiagramValidationError as exc:
        errors.extend(exc.errors)
    spec_failed = bool(errors)
    content = {"title": reply.title.strip(),
               "subtitle": reply.subtitle.strip(),
               "diagram": diagram_raw}
    # Слот-контракт шаблона держит капы title/subtitle (и required title).
    # Пустой слот diagram — следствие непрошедшего спека, а не отдельная беда:
    # претензию к слоту глушим, чтобы модель чинила настоящую причину. Флаг
    # считаем ДО цикла — иначе первая же добавленная претензия глушила вторую.
    for e in library.validate_content("diagram", content):
        if e.slot != "diagram" or not spec_failed:
            errors.append(f"{e.code}:{e.slot} {e.detail}".strip())
    return content, errors


def _content_errors(library: TemplateLibrary, content: dict, *,
                    kind: str = "") -> list[str]:
    """Претензии к уже готовому контенту (скип-путь): слоты + сам спек.

    Чужой ``kind`` — тоже претензия: иначе слайд со старой схемой проскакивал
    мимо модели, и смена типа автором ничего не меняла."""
    errors = [f"{e.code}:{e.slot}" for e in
              library.validate_content("diagram", content)]
    try:
        spec = parse_diagram(content.get("diagram", ""))
        if kind and spec.kind != kind:
            errors.append(f"kind: {spec.kind} вместо {kind}")
    except DiagramValidationError as exc:
        errors.extend(exc.errors)
    return errors
