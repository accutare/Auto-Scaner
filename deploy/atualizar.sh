#!/usr/bin/env bash
# Chamado pelo cron a cada 5 minutos: baixa versão/configuração nova do GitHub e reinicia se precisar.
set -euo pipefail
cd /opt/docflow
git fetch -q origin
CODIGO=""
if [ "$(git rev-parse HEAD)" != "$(git rev-parse '@{u}')" ]; then
  git pull -q --ff-only
  CODIGO="sim"
fi
CONFIG=$(bash deploy/aplicar-config.sh)
if [ -n "$CODIGO" ] || [ -n "$CONFIG" ]; then
  echo "$(date -Is) atualizando (codigo=${CODIGO:-nao} config=${CONFIG:-nao})"
  docker compose up -d --build
fi
