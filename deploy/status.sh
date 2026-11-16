#!/usr/bin/env bash
# Gera dados/status.txt (sem chaves nem senhas) e envia para o Dropbox: Docflow/Status do servidor.txt
cd /opt/docflow
mkdir -p dados
{
  echo "STATUS DO SERVIDOR DOCFLOW - $(date '+%d/%m/%Y %H:%M:%S %Z')"
  echo
  echo "== Versão do programa =="; git log -1 --format='%h %ad %s' --date=format:'%d/%m %H:%M'
  echo
  echo "== Serviços =="; docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}' 2>&1
  echo
  echo "== Endereço do app =="; grep -E '^(DOMINIO|WEB|AUTOMATICO|PASTA_ENTRADA|PASTA_RAIZ_CODIGOS)=' .env
  echo
  echo "== Firewall =="; ufw status 2>&1 | head -n 12
  echo
  echo "== Últimas atualizações =="; tail -n 15 /var/log/docflow-atualizar.log 2>&1
  echo
  echo "== App (web) =="; docker compose logs --no-color --tail=25 web 2>&1 | sed -E 's/(senha|password)[^ ]*/[oculto]/Ig'
  echo
  echo "== HTTPS (caddy) =="; docker compose logs --no-color --tail=25 caddy 2>&1
} > dados/status.txt 2>&1
if [ -n "$(docker compose ps -q web 2>/dev/null)" ]; then
  docker compose exec -T web python -m app.enviar_status /data/status.txt >/dev/null 2>&1 \
    || docker compose run --rm --no-deps -T web python -m app.enviar_status /data/status.txt >/dev/null 2>&1
else
  docker compose run --rm --no-deps -T web python -m app.enviar_status /data/status.txt >/dev/null 2>&1
fi
