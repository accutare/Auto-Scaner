#!/usr/bin/env bash
# Configura as chaves do Docflow no servidor, sem precisar editar arquivo à mão.
# Rode dentro do servidor:   cd /opt/docflow && bash deploy/configurar.sh
#
# O que faz:
#   1. Pede a chave da API da Anthropic e testa se funciona
#   2. Pede o App key e o App secret do Dropbox, abre a autorização e gera o refresh token
#   3. Testa o Dropbox e mostra em qual conta entrou
#   4. Salva tudo no arquivo .env (só o root consegue ler)
# As chaves aparecem na tela enquanto você cola (para conferir); não tire print dessa parte.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env
[ -f "$ENV_FILE" ] || cp .env.example "$ENV_FILE"
chmod 600 "$ENV_FILE"

definir() {
  # Grava/atualiza VAR=valor no .env sem passar o valor por sed (a chave pode ter qualquer caractere)
  NOME="$1" VALOR="$2" ARQ="$ENV_FILE" python3 - <<'PY'
import os
nome, valor, arq = os.environ["NOME"], os.environ["VALOR"], os.environ["ARQ"]
linhas = open(arq, encoding="utf-8").read().splitlines()
achou = False
for i, l in enumerate(linhas):
    if l.startswith(nome + "="):
        linhas[i] = f"{nome}={valor}"
        achou = True
if not achou:
    linhas.append(f"{nome}={valor}")
open(arq, "w", encoding="utf-8").write("\n".join(linhas) + "\n")
PY
}

json() {  # json <campo>  (lê JSON da entrada padrão)
  python3 -c "import json,sys; print(json.load(sys.stdin).get('$1') or '')" 2>/dev/null || true
}

echo
echo "As chaves vão aparecer na tela enquanto você cola. Não tire print desta parte."
echo "=== 1/2 Anthropic (IA) ==="
while true; do
  read -r -p "Cole a chave da API da Anthropic (começa com sk-ant-) e aperte Enter: " AKEY
  AKEY="$(echo "$AKEY" | tr -d '[:space:]')"
  if [ -z "$AKEY" ]; then
    echo "Nada foi colado. Copie a chave de novo e cole com Cmd+V (ou clique com o botão direito > Colar)."
    continue
  fi
  case "$AKEY" in sk-ant-*) ;; *)
    echo "Isso não parece a chave da Anthropic: ela começa com sk-ant-. Tente de novo."
    continue ;;
  esac
  CODIGO=$(curl -s -o /dev/null -w "%{http_code}" https://api.anthropic.com/v1/models \
    -H "x-api-key: $AKEY" -H "anthropic-version: 2023-06-01" || true)
  if [ "$CODIGO" = "200" ]; then
    definir ANTHROPIC_API_KEY "$AKEY"
    echo "OK: chave da Anthropic funcionando."
    break
  fi
  case "$CODIGO" in
    401) echo "A Anthropic recusou a chave (inválida ou incompleta). Copie de novo a chave inteira." ;;
    000) echo "O servidor não conseguiu falar com a Anthropic (internet). Tente de novo em instantes." ;;
    *)   echo "A chave não funcionou (resposta $CODIGO). Tente de novo." ;;
  esac
done

echo
echo "=== 2/2 Dropbox ==="
read -r -p "Cole o App key do Dropbox e aperte Enter: " DKEY
DKEY="$(echo "$DKEY" | tr -d '[:space:]')"
read -r -p "Cole o App secret do Dropbox e aperte Enter: " DSECRET
DSECRET="$(echo "$DSECRET" | tr -d '[:space:]')"

while true; do
  echo
  echo "Abra este link no navegador (logado na conta do Dropbox do escritório), clique em"
  echo "'Continuar' e depois em 'Permitir'. Ele vai mostrar um código."
  echo
  echo "  https://www.dropbox.com/oauth2/authorize?client_id=${DKEY}&response_type=code&token_access_type=offline"
  echo
  read -r -p "Cole aqui o código que apareceu e aperte Enter: " DCODE
  DCODE="$(echo "$DCODE" | tr -d '[:space:]')"
  RESP=$(curl -s https://api.dropboxapi.com/oauth2/token \
    -d code="$DCODE" -d grant_type=authorization_code -u "$DKEY:$DSECRET" || true)
  REFRESH=$(echo "$RESP" | json refresh_token)
  ACCESS=$(echo "$RESP" | json access_token)
  if [ -n "$REFRESH" ]; then
    break
  fi
  echo "Não deu certo: $(echo "$RESP" | json error_description)"
  echo "Os códigos valem uma vez só. Vamos gerar outro."
done

CONTA=$(curl -s -X POST https://api.dropboxapi.com/2/users/get_current_account \
  -H "Authorization: Bearer $ACCESS" || true)
NOME=$(echo "$CONTA" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['name']['display_name'], '-', d['email'])" 2>/dev/null || echo "?")

definir DROPBOX_APP_KEY "$DKEY"
definir DROPBOX_APP_SECRET "$DSECRET"
definir DROPBOX_REFRESH_TOKEN "$REFRESH"
chmod 600 "$ENV_FILE"

echo "OK: Dropbox conectado na conta: $NOME"
echo
echo "Pronto. As chaves foram salvas em $(pwd)/$ENV_FILE (só o root consegue ler)."
echo "Se a conta do Dropbox acima não for a do escritório, rode este script de novo."
