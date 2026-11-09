#!/usr/bin/env bash
# Chamado pelo cron a cada 5 minutos: se houver versão nova no GitHub, baixa e reinicia o programa.
set -euo pipefail
cd /opt/docflow
git fetch -q origin
LOCAL=$(git rev-parse HEAD)
REMOTO=$(git rev-parse '@{u}')
if [ "$LOCAL" != "$REMOTO" ]; then
  echo "$(date -Is) atualizando $LOCAL -> $REMOTO"
  git pull -q --ff-only
  docker compose up -d --build
fi
