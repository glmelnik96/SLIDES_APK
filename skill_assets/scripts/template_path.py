#!/usr/bin/env python3
"""
template_path.py — единая точка резолва пути к шаблону Cloud.ru.

Зачем: шаблон (29 MB) не входит в skill (лимит) и лежит вне него. Раньше его
приходилось каждый раз подгружать в чат. Этот резолвер находит шаблон сам —
не нужно прикладывать файл, если он есть на диске.

Приоритет поиска (первый существующий путь выигрывает):
  1. $CLOUD_RU_TEMPLATE                       — явный override (env var)
  2. brand/template-version.json → "template_path"  — настраиваемый путь (1 строка)
  3. известные кандидаты (рядом со skill, на десктопе, в cwd, в чате)

Использование:
  CLI:    python3 scripts/template_path.py          # печатает абсолютный путь
  import: from template_path import resolve_template
"""
import os
import sys
import json

TEMPLATE_FILENAME = "Cloud.ru_Template_2026.pptx"


def _skill_root():
    # scripts/ лежит внутри корня skill → корень = на уровень выше
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _from_version_json():
    """Читает template_path из brand/template-version.json, если задан."""
    here = _skill_root()
    candidates = [
        os.path.join(here, "brand", "template-version.json"),
        os.path.join(os.getcwd(), "brand", "template-version.json"),
        os.path.join(os.getcwd(), "pptx-skill", "brand", "template-version.json"),
    ]
    for path in candidates:
        try:
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    cfg = json.load(f)
                tp = cfg.get("template_path")
                if tp:
                    return os.path.expanduser(os.path.expandvars(tp))
        except Exception:
            continue
    return None


def _candidates():
    root = _skill_root()
    cwd = os.getcwd()
    paths = []

    # 1. env override
    env = os.environ.get("CLOUD_RU_TEMPLATE")
    if env:
        paths.append(os.path.expanduser(os.path.expandvars(env)))

    # 2. путь из config
    cfg = _from_version_json()
    if cfg:
        paths.append(cfg)

    # 3. рядом со skill: <skill>/../template/...  (работает в dev-репо)
    paths.append(os.path.join(root, "..", "template", TEMPLATE_FILENAME))
    paths.append(os.path.join(root, "template", TEMPLATE_FILENAME))

    # 4. известный абсолютный путь на десктопе пользователя
    paths.append(os.path.expanduser(
        "~/Desktop/Презентации в ИИ 2/template/" + TEMPLATE_FILENAME))

    # 5. cwd (если шаблон лежит рядом с запуском или подгружен в чат)
    paths.append(os.path.join(cwd, "template", TEMPLATE_FILENAME))
    paths.append(os.path.join(cwd, TEMPLATE_FILENAME))

    return paths


def resolve_template(required=True):
    """Возвращает абсолютный путь к шаблону. None/исключение если не найден."""
    for p in _candidates():
        try:
            if p and os.path.isfile(p):
                return os.path.abspath(p)
        except Exception:
            continue
    if required:
        tried = "\n  ".join(_candidates())
        raise FileNotFoundError(
            "Шаблон Cloud.ru не найден. Проверены пути:\n  " + tried +
            "\n\nРешения:\n"
            "  • задай brand/template-version.json → \"template_path\"\n"
            "  • или экспортируй CLOUD_RU_TEMPLATE=/path/to/template.pptx\n"
            "  • или подгрузи файл в чат / положи в текущую папку"
        )
    return None


if __name__ == "__main__":
    try:
        print(resolve_template())
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
