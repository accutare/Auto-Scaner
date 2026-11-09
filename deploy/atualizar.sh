#!/usr/bin/env bash
# Chamado pelo cron a cada 5 minutos. Baixa versão/configuração nova do GitHub e:
#   AUTOMATICO=sim -> mantém o programa ligado (reinicia se mudou algo);
#   AUTOMATICO=nao -> mantém desligado; se TESTE_RODADA mudou, processa 1 arquivo.
set -euo pipefail
cd /opt/docflow
exec 9>/var/lock/docflow-atualizar.lock
flock -n 9 || exit 0

git fetch -q origin
CODIGO=""
if [ "$(git rev-parse HEAD)" != "$(git rev-parse '@{u}')" ]; then
  git pull -q --ff-only
  chmod +x deploy/*.sh
  CODIGO="sim"
fi
CONFIG=$(bash deploy/aplicar-config.sh)
valor() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
[ -n "$CODIGO" ] && docker compose build -q

if [ "$(valor AUTOMATICO)" = "sim" ]; then
  if [ -n "$CODIGO$CONFIG" ] || [ -z "$(docker compose ps -q docflow)" ]; then
    echo "$(date -Is) ligando/atualizando o modo automático"
    docker compose up -d
  fi
else
  if [ -n "$(docker compose ps -q docflow)" ]; then
    echo "$(date -Is) desligando o modo automático"
    docker compose stop
  fi
  mkdir -p dados
  LIMPEZA=$(valor LIMPEZA_RODADA)
  ULTIMA_LIMPEZA=$(cat dados/ultima-limpeza 2>/dev/null || true)
  if [ -n "$LIMPEZA" ] && [ "$LIMPEZA" != "$ULTIMA_LIMPEZA" ]; then
    echo "$LIMPEZA" > dados/ultima-limpeza
    echo "$(date -Is) limpeza do teste $LIMPEZA"
    docker compose run --rm docflow python -m app.limpar 2>&1 | tee -a dados/testes.log
  fi
  RODADA=$(valor TESTE_RODADA)
  ULTIMA=$(cat dados/ultima-rodada 2>/dev/null || true)
  if [ -n "$RODADA" ] && [ "$RODADA" != "$ULTIMA" ]; then
    echo "$RODADA" > dados/ultima-rodada
    echo "$(date -Is) rodada de teste $RODADA"
    bash deploy/rodar-teste.sh
  fi
fi
