#!/usr/bin/env bash
# Liga o Docflow no servidor e ativa a atualização automática. Rode UMA vez:
#   cd /opt/docflow && git pull && bash deploy/ligar.sh
#
# Depois disso, sem precisar de Terminal:
#   - o programa fica ligado e olha a pasta de entrada (definida em deploy/config-servidor.env) a cada minuto;
#   - volta sozinho se o servidor reiniciar;
#   - a cada 5 minutos o servidor confere se há versão ou configuração nova no GitHub e se atualiza sozinho.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Falta o .env: rode antes bash deploy/configurar.sh"; exit 1; }

bash deploy/aplicar-config.sh >/dev/null
docker compose up -d --build

cat > /etc/cron.d/docflow-atualizar <<'CRON'
*/5 * * * * root /opt/docflow/deploy/atualizar.sh >> /var/log/docflow-atualizar.log 2>&1
CRON
chmod 644 /etc/cron.d/docflow-atualizar
chmod +x deploy/atualizar.sh deploy/aplicar-config.sh

echo
grep -E "^PASTA_ENTRADA=" .env | sed 's/^/Olhando a pasta: /'
echo "Pronto: o Docflow está ligado e se atualiza sozinho. Pode fechar o Terminal."
