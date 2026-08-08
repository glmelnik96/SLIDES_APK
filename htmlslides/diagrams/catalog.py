"""Каталог типов диаграмм: метаданные для UI-пикера, превью и LLM-промпта.

Единый реестр ВСЕХ запланированных типов (включая будущие волны): UI показывает
полный «большой блок выбора» с пометкой «скоро», LLM-промпт (фаза 2) получает
только реализованные. Новый тип = модель в schema.py + layout-функция в
engine/diagram.js + карточка здесь.

``sample`` каждой доступной карточки — валидный DiagramSpec: он питает превью
пикера (templates_api), пустой слот черновика (draft_render) и тесты.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class DiagramType:
    kind: str
    display_name: str          # русское имя в пикере
    when_to_use: str           # «когда брать» — для пикера И LLM-промпта
    phase: int                 # волна реализации (1 — MVP)
    sample: dict | None = None # валидный пример; None = тип ещё не реализован

    @property
    def available(self) -> bool:
        return self.sample is not None


_FLOWCHART_SAMPLE = {
    "kind": "flowchart", "direction": "right",
    "nodes": [
        {"id": "start", "label": "Заявка клиента", "shape": "start"},
        {"id": "check", "label": "Проверка данных", "shape": "process"},
        {"id": "ok", "label": "Данные полные?", "shape": "decision"},
        {"id": "fix", "label": "Запрос уточнений", "shape": "process"},
        {"id": "deal", "label": "Оформление договора", "shape": "process", "accent": True},
        {"id": "done", "label": "Услуга подключена", "shape": "end"},
    ],
    "edges": [
        {"from": "start", "to": "check"},
        {"from": "check", "to": "ok"},
        {"from": "ok", "to": "deal", "label": "да"},
        {"from": "ok", "to": "fix", "label": "нет"},
        {"from": "fix", "to": "check"},
        {"from": "deal", "to": "done"},
    ],
}

_PROCESS_SAMPLE = {
    "kind": "process",
    "nodes": [
        {"id": "s1", "label": "Аудит инфраструктуры"},
        {"id": "s2", "label": "План миграции"},
        {"id": "s3", "label": "Пилотный перенос"},
        {"id": "s4", "label": "Промышленная миграция", "accent": True},
        {"id": "s5", "label": "Сопровождение"},
    ],
}

_CYCLE_SAMPLE = {
    "kind": "cycle",
    "nodes": [
        {"id": "c1", "label": "Сбор метрик"},
        {"id": "c2", "label": "Анализ узких мест"},
        {"id": "c3", "label": "Оптимизация", "accent": True},
        {"id": "c4", "label": "Контроль эффекта"},
    ],
}

_FUNNEL_SAMPLE = {
    "kind": "funnel",
    "nodes": [
        {"id": "f1", "label": "Посетители сайта", "value": "12000"},
        {"id": "f2", "label": "Регистрации", "value": "3400"},
        {"id": "f3", "label": "Активации", "value": "1100"},
        {"id": "f4", "label": "Платящие клиенты", "value": "280", "accent": True},
    ],
}

_HIERARCHY_SAMPLE = {
    "kind": "hierarchy",
    "nodes": [
        {"id": "ceo", "label": "Директор платформы", "accent": True},
        {"id": "dev", "label": "Разработка"},
        {"id": "ops", "label": "Эксплуатация"},
        {"id": "sec", "label": "Безопасность"},
        {"id": "be", "label": "Backend"},
        {"id": "fe", "label": "Frontend"},
        {"id": "sre", "label": "SRE"},
    ],
    "edges": [
        {"from": "ceo", "to": "dev"},
        {"from": "ceo", "to": "ops"},
        {"from": "ceo", "to": "sec"},
        {"from": "dev", "to": "be"},
        {"from": "dev", "to": "fe"},
        {"from": "ops", "to": "sre"},
    ],
}


_MATRIX_SAMPLE = {
    "kind": "matrix",
    "nodes": [
        {"id": "q1", "label": "Быстрые победы", "accent": True},
        {"id": "q2", "label": "Стратегические проекты"},
        {"id": "q3", "label": "Фоновые задачи"},
        {"id": "q4", "label": "Неблагодарная работа"},
    ],
    "meta": {"x_axis": "Усилия", "y_axis": "Эффект"},
}

_PYRAMID_SAMPLE = {
    "kind": "pyramid",
    "nodes": [
        {"id": "p1", "label": "Видение", "accent": True},
        {"id": "p2", "label": "Стратегические цели"},
        {"id": "p3", "label": "Ключевые инициативы"},
        {"id": "p4", "label": "Операционные задачи"},
    ],
}

_HUB_SPOKE_SAMPLE = {
    "kind": "hub_spoke",
    "nodes": [
        {"id": "hub", "label": "Платформа Evolution", "accent": True},
        {"id": "s1", "label": "Kubernetes"},
        {"id": "s2", "label": "Базы данных"},
        {"id": "s3", "label": "Объектное хранилище"},
        {"id": "s4", "label": "AI-сервисы"},
        {"id": "s5", "label": "Мониторинг"},
    ],
}

_COMPARISON_SAMPLE = {
    "kind": "comparison",
    "nodes": [
        {"id": "a1", "label": "Капитальные затраты на железо", "lane": "On-premise"},
        {"id": "a2", "label": "Масштабирование месяцами", "lane": "On-premise"},
        {"id": "a3", "label": "Своя команда эксплуатации", "lane": "On-premise"},
        {"id": "b1", "label": "Оплата по потреблению", "lane": "Облако", "accent": True},
        {"id": "b2", "label": "Масштабирование за минуты", "lane": "Облако"},
        {"id": "b3", "label": "Управляемые сервисы", "lane": "Облако"},
    ],
}

_VENN_SAMPLE = {
    "kind": "venn",
    "nodes": [
        {"id": "v1", "label": "Скорость разработки"},
        {"id": "v2", "label": "Надёжность эксплуатации"},
    ],
    "meta": {"center_label": "DevOps-культура"},
}

_SWIMLANES_SAMPLE = {
    "kind": "swimlanes",
    "nodes": [
        {"id": "w1", "label": "Заявка на доступ", "lane": "Клиент"},
        {"id": "w2", "label": "Проверка договора", "lane": "Менеджер"},
        {"id": "w3", "label": "Выделение квоты", "lane": "Инженер"},
        {"id": "w4", "label": "Настройка окружения", "lane": "Инженер", "accent": True},
        {"id": "w5", "label": "Подтверждение запуска", "lane": "Клиент"},
    ],
    "edges": [
        {"from": "w1", "to": "w2"},
        {"from": "w2", "to": "w3"},
        {"from": "w3", "to": "w4"},
        {"from": "w4", "to": "w5"},
    ],
}


CATALOG: tuple[DiagramType, ...] = (
    # ---- волна 1 (реализованы) ----
    DiagramType("flowchart", "Блок-схема",
                "Процесс с ветвлениями и условиями: заявки, согласования, "
                "алгоритмы «если — то». Стрелки показывают ход, ромб — развилку.",
                1, _FLOWCHART_SAMPLE),
    DiagramType("process", "Этапы процесса",
                "Линейная последовательность шагов без ветвлений: дорожная карта "
                "внедрения, порядок работ, шаги онбординга.",
                1, _PROCESS_SAMPLE),
    DiagramType("cycle", "Цикл",
                "Повторяющийся замкнутый процесс: PDCA, итерации, жизненный цикл. "
                "3–8 стадий по кругу.",
                1, _CYCLE_SAMPLE),
    DiagramType("funnel", "Воронка",
                "Сужение от этапа к этапу с числами: продажи, найм, конверсия. "
                "Ширина слоя — величина этапа.",
                1, _FUNNEL_SAMPLE),
    DiagramType("hierarchy", "Оргсхема / дерево",
                "Иерархия «кто кому подчинён» или декомпозиция: оргструктура, "
                "состав продукта, дерево решений без условий.",
                1, _HIERARCHY_SAMPLE),
    # ---- волна 2 (реализованы) ----
    DiagramType("matrix", "Матрица 2×2",
                "Позиционирование по двум осям: важно/срочно, риск/эффект, "
                "цена/качество.", 2, _MATRIX_SAMPLE),
    DiagramType("pyramid", "Пирамида",
                "Уровни от основания к вершине: приоритеты, зрелость, иерархия "
                "потребностей.", 2, _PYRAMID_SAMPLE),
    DiagramType("hub_spoke", "Хаб и лучи",
                "Центральная сущность и связанные с ней элементы: экосистема "
                "продукта, интеграции, стейкхолдеры.", 2, _HUB_SPOKE_SAMPLE),
    DiagramType("comparison", "Сравнение сторон",
                "Два подхода бок о бок: до/после, мы/конкуренты, on-prem/облако.",
                2, _COMPARISON_SAMPLE),
    DiagramType("venn", "Диаграмма Венна",
                "Пересечение 2–3 множеств: общие черты и различия, зоны "
                "ответственности.", 2, _VENN_SAMPLE),
    DiagramType("swimlanes", "Дорожки процесса",
                "Процесс, разложенный по исполнителям: кто какой шаг делает, "
                "передачи между отделами.", 2, _SWIMLANES_SAMPLE),
    # ---- волна 3+ ----
    DiagramType("gantt_lite", "План-график",
                "Работы на шкале времени: этапы проекта с длительностями.", 3),
    DiagramType("mindmap", "Ментальная карта",
                "Радиальная структура идей вокруг центральной темы.", 3),
    DiagramType("network", "Граф связей",
                "Произвольная сеть узлов: интеграции, зависимости сервисов.", 3),
    DiagramType("steps", "Лестница",
                "Ступени роста по диагонали: уровни зрелости, план развития.", 3),
)

_BY_KIND = {t.kind: t for t in CATALOG}

AVAILABLE_KINDS: tuple[str, ...] = tuple(t.kind for t in CATALOG if t.available)


def get_type(kind: str) -> DiagramType:
    try:
        return _BY_KIND[kind]
    except KeyError:
        raise KeyError(f"unknown diagram kind: {kind}") from None


def sample_spec(kind: str) -> dict:
    """Валидный пример спека для превью/тестов. KeyError на нереализованном."""
    t = get_type(kind)
    if t.sample is None:
        raise KeyError(f"diagram kind not implemented yet: {kind}")
    # копия: сэмплы — константы каталога, наружу отдаём независимый dict
    return copy.deepcopy(t.sample)


def catalog_for_ui() -> list[dict]:
    """Полный каталог для «большого блока выбора типов» в редакторе.

    ``sample`` (None у нереализованных) нужен клиенту, чтобы сразу материализовать
    выбранный тип в поля слайда — пользователь правит пример, а не пустоту."""
    return [{
        "kind": t.kind,
        "display_name": t.display_name,
        "when_to_use": t.when_to_use,
        "available": t.available,
        "sample": copy.deepcopy(t.sample),
    } for t in CATALOG]
