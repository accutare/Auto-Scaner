#!/usr/bin/env bash
# Processa UM arquivo da pasta de entrada (de verdade: cria as pastas e move) e mostra o resultado.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dados
echo "$(date -Is) teste com 1 arquivo" >> dados/testes.log
docker compose run --rm docflow python -m app.main --uma-vez --limite 1 2>&1 | tee -a dados/testes.log
