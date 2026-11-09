#!/usr/bin/env bash
# Liga o Docflow no automático (24h) e ativa a atualização automática. Rode UMA vez no servidor:
#   cd /opt/docflow && git pull && bash deploy/ligar.sh
#
# Depois disso:
#   - o programa olha a pasta Scans a cada minuto e organiza os documentos sozinho;
#   - volta sozinho se o servidor reiniciar;
#   - a cada 5 minutos o servidor confere se há versão nova no GitHub e se atualiza sozinho.
set -euo pipefail
cd "$(dirname "$0")/.."

definir() {
  NOME="$1" VALOR="$2" ARQ=.env python3 - <<'PY'
import os
nome, valor, arq = os.environ["NOME"], os.environ["VALOR"], os.environ["ARQ"]
linhas = open(arq, encoding="utf-8").read().splitlines()
for i, l in enumerate(linhas):
    if l.startswith(nome + "="):
        linhas[i] = f"{nome}={valor}"
        break
else:
    linhas.append(f"{nome}={valor}")
open(arq, "w", encoding="utf-8").write("\n".join(linhas) + "\n")
PY
}

[ -f .env ] || { echo "Falta o .env: rode antes bash deploy/configurar.sh"; exit 1; }

# Fase de teste: tudo em pastas com TESTE no nome; o Client Data não é tocado.
definir SIMULACAO false
definir PASTA_ENTRADA "/Scans"
definir PASTA_CLIENTES "/TESTE - Clientes"
definir PASTA_REVISAR "/TESTE - Docflow/Revisar"
definir PASTA_ERROS "/TESTE - Docflow/Erros"
definir PASTA_CONTROLE "/TESTE - Docflow/Controle"
chmod 600 .env

docker compose up -d --build

cat > /etc/cron.d/docflow-atualizar <<'CRON'
*/5 * * * * root /opt/docflow/deploy/atualizar.sh >> /var/log/docflow-atualizar.log 2>&1
CRON
chmod 644 /etc/cron.d/docflow-atualizar
chmod +x deploy/atualizar.sh

echo
echo "Pronto: o Docflow está ligado no automático e se atualiza sozinho."
echo "Pode fechar o Terminal."
