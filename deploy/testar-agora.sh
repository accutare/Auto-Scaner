#!/usr/bin/env bash
# Rode UMA vez:  cd /opt/docflow && git pull && bash deploy/testar-agora.sh
#   1. aplica a configuração de teste (pasta de entrada Scans, destino nas pastas TESTE);
#   2. processa 1 arquivo da Scans e mostra o resultado na tela;
#   3. ativa a atualização automática do servidor, para os próximos testes e a ativação final serem
#      feitos sem Terminal (mudando deploy/config-servidor.env no GitHub).
# O programa NÃO fica ligado no automático.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Falta o .env: rode antes bash deploy/configurar.sh"; exit 1; }
chmod +x deploy/*.sh
bash deploy/aplicar-config.sh >/dev/null
mkdir -p dados
grep -E "^TESTE_RODADA=" .env | cut -d= -f2- > dados/ultima-rodada
docker compose build -q
docker compose stop >/dev/null 2>&1 || true

cat > /etc/cron.d/docflow-atualizar <<'CRON'
*/5 * * * * root /opt/docflow/deploy/atualizar.sh >> /var/log/docflow-atualizar.log 2>&1
CRON
chmod 644 /etc/cron.d/docflow-atualizar

bash deploy/rodar-teste.sh
echo "Teste concluído. O programa NÃO ficou ligado. Pode fechar o Terminal."
