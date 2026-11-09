#!/usr/bin/env bash
# Copia as linhas de deploy/config-servidor.env para o .env (mantendo as chaves que já estão lá).
# Imprime "mudou" se algo foi alterado.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
import os
cfg = {}
for l in open("deploy/config-servidor.env", encoding="utf-8"):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1)
        cfg[k.strip()] = v.strip()
antes = open(".env", encoding="utf-8").read()
linhas = antes.splitlines()
for k, v in cfg.items():
    for i, l in enumerate(linhas):
        if l.startswith(k + "="):
            linhas[i] = f"{k}={v}"
            break
    else:
        linhas.append(f"{k}={v}")
depois = "\n".join(linhas) + "\n"
if depois != antes:
    open(".env", "w", encoding="utf-8").write(depois)
    print("mudou")
PY
chmod 600 .env
