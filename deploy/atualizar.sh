#!/usr/bin/env bash
# Chamado pelo cron a cada 1 minuto. Baixa versão/configuração nova do GitHub e aplica:
#   WEB=sim        -> mantém o app do scanner no ar (web + HTTPS) e libera as portas 80/443 no firewall;
#   AUTOMATICO=sim -> mantém a leitura automática dos scans ligada; nao -> desligada;
#   com AUTOMATICO=nao, LIMPEZA_RODADA e TESTE_RODADA disparam uma limpeza / um teste quando mudam.
set -euo pipefail
cd /opt/docflow
exec 9>/var/lock/docflow-atualizar.lock
flock -n 9 || exit 0

# Confere a cada 1 minuto (o próprio script mantém o agendamento assim)
CRON="* * * * * root /opt/docflow/deploy/atualizar.sh >> /var/log/docflow-atualizar.log 2>&1"
if [ "$(cat /etc/cron.d/docflow-atualizar 2>/dev/null)" != "$CRON" ]; then
  echo "$CRON" > /etc/cron.d/docflow-atualizar
  chmod 644 /etc/cron.d/docflow-atualizar
fi

git fetch -q origin
CODIGO=""
if [ "$(git rev-parse HEAD)" != "$(git rev-parse '@{u}')" ]; then
  git pull -q --ff-only
  chmod +x deploy/*.sh
  CODIGO="sim"
fi
CONFIG=$(bash deploy/aplicar-config.sh)
valor() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
MUDOU="$CODIGO$CONFIG"
[ -n "$CODIGO" ] && docker compose build -q

rodando() { [ -n "$(docker compose ps -q "$1" 2>/dev/null)" ]; }

if [ "$(valor WEB)" = "sim" ]; then
  if command -v ufw >/dev/null 2>&1; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; fi
  if [ -n "$MUDOU" ] || ! rodando web || ! rodando caddy; then
    echo "$(date -Is) app do scanner: ligando/atualizando"
    docker compose up -d web caddy
  fi
fi

if [ "$(valor AUTOMATICO)" = "sim" ]; then
  if [ -n "$MUDOU" ] || ! rodando docflow; then
    echo "$(date -Is) leitura automática: ligando/atualizando"
    docker compose up -d docflow
  fi
else
  if rodando docflow; then
    echo "$(date -Is) leitura automática: desligando"
    docker compose stop docflow
  fi
  mkdir -p dados
  LIMPEZA=$(valor LIMPEZA_RODADA)
  ULTIMA_LIMPEZA=$(cat dados/ultima-limpeza 2>/dev/null || true)
  if [ -n "$LIMPEZA" ] && [ "$LIMPEZA" != "$ULTIMA_LIMPEZA" ]; then
    echo "$LIMPEZA" > dados/ultima-limpeza
    echo "$(date -Is) limpeza do teste $LIMPEZA"
    ALVO=$(valor LIMPEZA_ALVO)
    if [ -n "$ALVO" ]; then
      docker compose run --rm docflow python -m app.limpar "$ALVO" 2>&1 | tee -a dados/testes.log
    else
      docker compose run --rm docflow python -m app.limpar 2>&1 | tee -a dados/testes.log
    fi
  fi
  RODADA=$(valor TESTE_RODADA)
  ULTIMA=$(cat dados/ultima-rodada 2>/dev/null || true)
  if [ -n "$RODADA" ] && [ "$RODADA" != "$ULTIMA" ]; then
    echo "$RODADA" > dados/ultima-rodada
    echo "$(date -Is) rodada de teste $RODADA"
    bash deploy/rodar-teste.sh
  fi
fi
