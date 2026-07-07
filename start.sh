#!/usr/bin/env bash
# Запуск Slides App. Работает и без предварительной активации venv:
# сам выбирает интерпретатор — сначала .venv проекта, затем python3, затем python.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
elif command -v python >/dev/null 2>&1; then
  PY="python"
else
  echo "Python не найден. Установите Python 3.10+ (см. README)." >&2
  exit 1
fi

exec "$PY" -m webapp
