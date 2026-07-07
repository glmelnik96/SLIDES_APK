@echo off
REM Запуск Slides App. Предпочитает .venv проекта, иначе берёт python из PATH.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m webapp
) else (
  python -m webapp
)
