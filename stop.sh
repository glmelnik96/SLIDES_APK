#!/usr/bin/env bash
# Остановить Slides App: завершает процесс, СЛУШАЮЩИЙ порт приложения (по умолчанию 8000).
# Фильтр по состоянию LISTEN, чтобы не задеть клиентские соединения к этому порту.
set -euo pipefail

PORT="${SLIDES_PORT:-8000}"

find_pids() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true
}

pids="$(find_pids)"
if [[ -z "$pids" ]]; then
  echo "Slides App не запущен (порт $PORT свободен)."
  exit 0
fi

echo "Останавливаю Slides App (PID: $(echo "$pids" | tr '\n' ' '))…"
kill $pids 2>/dev/null || true

# Ждём корректного завершения до ~10 секунд.
for _ in $(seq 1 20); do
  [[ -z "$(find_pids)" ]] && { echo "Остановлено."; exit 0; }
  sleep 0.5
done

# Принудительно, если не завершился сам.
echo "Graceful-стоп не сработал, завершаю принудительно…"
kill -9 $(find_pids) 2>/dev/null || true
echo "Остановлено (forced)."
