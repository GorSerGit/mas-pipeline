#!/usr/bin/env bash
# Полный запуск MAS Pipeline: build -> transfer -> up -> wait -> tunnel -> test.
#
#   ./scripts/start_all.sh                # всё, включая туннель
#   SKIP_BUILD=1 ./scripts/start_all.sh   # без пересборки (образы уже есть)
#   SKIP_TRANSFER=1 ./scripts/start_all.sh# образы уже на сервере
#   NO_TUNNEL=1 ./scripts/start_all.sh    # не открывать SSH-туннель
#   RUN_TESTS=1 ./scripts/start_all.sh    # + test_services.sh quick в конце
#
# Адрес сервера задаётся через REMOTE/SSH_PORT или в файле .env.deploy
# (не коммитится, пример — .env.deploy.example).
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
[ -f .env.deploy ] && . ./.env.deploy
REMOTE=${REMOTE:?Укажите REMOTE=user@host (или заполните .env.deploy)}
SSH_PORT=${SSH_PORT:-22}
REMOTE_DIR=${REMOTE_DIR:-mas-pipeline}
UI_PORT=${UI_PORT:-8088}
API_PORTS=(8101 8102 8103 8104 8105 8106 8107 8108 8109 8110)
COMPOSE_ARGS="-f docker-compose.yml -f docker-compose.ports.yml"

SSH_OPTS=(-p "$SSH_PORT" -o ServerAliveInterval=30)

# --- 0. Проверки окружения ---
command -v docker >/dev/null || { echo "Нет docker"; exit 1; }
docker info >/dev/null 2>&1  || { echo "Docker не отвечает (Docker Desktop запущен?)"; exit 1; }
command -v curl   >/dev/null || { echo "Нет curl"; exit 1; }

# --- 1. Сборка (опционально) ---
if [ "${SKIP_BUILD:-0}" != 1 ]; then
  echo "== 1. Сборка образов"
  docker compose build
else
  echo "== 1. Сборка пропущена (SKIP_BUILD=1)"
fi

# --- 2. Перенос (опционально) ---
if [ "${SKIP_TRANSFER:-0}" != 1 ]; then
  echo "== 2. Перенос образов и файлов на $REMOTE"
  REMOTE="$REMOTE" SSH_PORT="$SSH_PORT" REMOTE_DIR="$REMOTE_DIR" START=0 \
    ./scripts/transfer_images.sh
else
  echo "== 2. Перенос пропущен (SKIP_TRANSFER=1)"
fi

# --- 3. Определяем, как обращаться к docker на сервере ---
if ssh "${SSH_OPTS[@]}" "$REMOTE" 'docker info >/dev/null 2>&1'; then
  D="docker"
elif ssh "${SSH_OPTS[@]}" "$REMOTE" 'sudo -n docker info >/dev/null 2>&1'; then
  D="sudo -n docker"
else
  echo "Нет доступа к Docker на $REMOTE (нужна группа docker или NOPASSWD)" >&2
  exit 1
fi

run_remote() { ssh "${SSH_OPTS[@]}" "$REMOTE" "cd '$REMOTE_DIR' && $*"; }

# --- 4. Поднять стек ---
echo "== 3. Поднимаю стек на сервере"
run_remote "$D compose $COMPOSE_ARGS up -d --remove-orphans"
run_remote "$D compose $COMPOSE_ARGS ps"

# --- 5. Ждём health ---
echo "== 4. Жду /health всех сервисов (до 180 с)"
deadline=$((SECONDS+180))
while :; do
  ok=0; fail_list=()
  for p in "${API_PORTS[@]}"; do
    if curl -fsS -m 2 "http://$REMOTE_HOST:$p/health" >/dev/null 2>&1; then
      ok=$((ok+1))
    else
      fail_list+=("$p")
    fi
  done
  # health ходим через сам сервер: снаружи порты могут быть закрыты файрволом
  if [ "$ok" -eq 0 ]; then
    # fallback — проверяем изнутри сервера через curl в контейнере-клиенте
    inside=$(run_remote "for p in ${API_PORTS[*]}; do curl -fsS -m 2 http://localhost:\$p/health >/dev/null 2>&1 && echo -n ok || echo -n fail; done" || echo "")
    ok=$(echo "$inside" | grep -o ok | wc -l)
  fi
  echo "  $(date +%T) healthy: $ok/${#API_PORTS[@]}"
  [ "$ok" -ge "${#API_PORTS[@]}" ] && break
  [ $SECONDS -ge $deadline ] && { echo "  не все сервисы поднялись за 180 с"; break; }
  sleep 5
done

# --- 6. Туннель ---
if [ "${NO_TUNNEL:-0}" != 1 ]; then
  echo "== 5. Открываю SSH-туннель localhost:$UI_PORT -> $REMOTE:$UI_PORT"
  # закрываем старый туннель на этом порту, если был
  pkill -f "ssh.*-L $UI_PORT:localhost:$UI_PORT.*$REMOTE" 2>/dev/null || true
  sleep 1
  ssh -N -f -o ExitOnForwardFailure=yes "${SSH_OPTS[@]}" \
      -L "$UI_PORT:localhost:$UI_PORT" "$REMOTE"
  sleep 1
  if curl -fsS -m 3 -o /dev/null "http://localhost:$UI_PORT/"; then
    echo "  UI доступен: http://localhost:$UI_PORT"
  else
    echo "  ВНИМАНИЕ: UI на localhost:$UI_PORT не отвечает — проверьте логи ui"
  fi
fi

# --- 7. Тесты (опционально) ---
if [ "${RUN_TESTS:-0}" = 1 ]; then
  echo "== 6. test_services.sh quick"
  run_remote "./scripts/test_services.sh quick" || true
fi

cat <<EOF

=== Готово ===
UI:      http://localhost:$UI_PORT          (через SSH-туннель)
Прямой:  http://$REMOTE_HOST:$UI_PORT       (если порт открыт наружу)

Полезное:
  ./scripts/start_remote.sh ps              # статус контейнеров
  ./scripts/start_remote.sh logs backend    # логи сервиса
  ./scripts/start_remote.sh test            # test_services.sh quick
  ./scripts/start_remote.sh down            # остановить стек
  pkill -f 'ssh .*-L $UI_PORT'              # закрыть туннель
EOF