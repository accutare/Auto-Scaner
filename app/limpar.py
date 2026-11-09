"""Desfaz um teste: devolve os documentos para a pasta de entrada e só depois apaga as pastas de TESTE.

Regras de segurança:
  - Só mexe em pastas cujo nome começa com "TESTE -". Qualquer outra configuração faz o programa parar.
  - Primeiro devolve TODOS os documentos (com o nome original) para o lugar de onde vieram e confere que
    cada um chegou lá. Só então apaga as pastas de teste.
  - Uma pasta de teste só é apagada se nela não sobrar nenhum documento (só o Controle.xlsx, o backup e
    cópias de teste cujo original idêntico já está na pasta de entrada).
  - Apagar no Dropbox manda para a lixeira (recuperável).
  - O banco local do teste não é apagado: é renomeado para controle-teste-<data>.db.

Uso no servidor:  docker compose run --rm docflow python -m app.limpar                 (todas as pastas de teste)
                  docker compose run --rm docflow python -m app.limpar "/TESTE - Docflow"  (só esta)
"""
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime

PREFIXO_TESTE = "/TESTE - "
RE_HASH = re.compile(r"\[([0-9a-f]{8})\]")
GERADOS = re.compile(r"(^|/)(Controle\.xlsx|controle-\w+\.db)$")

log = logging.getLogger("docflow.limpar")


def raiz(caminho: str) -> str:
    return "/" + caminho.strip("/").split("/", 1)[0]


def origens_do_banco(caminho_db: str) -> dict[str, str]:
    """hash curto -> caminho de onde o documento veio (gravado no Controle)."""
    if not os.path.exists(caminho_db):
        return {}
    con = sqlite3.connect(caminho_db)
    try:
        linhas = con.execute(
            "SELECT hash_arquivo, caminho_original FROM documentos WHERE caminho_original IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {h[:8]: orig for h, orig in linhas if h and orig}


def nome_original(nome: str) -> str:
    """Tira o "[a1b2c3d4] " e o "DUPLICADO " que o programa acrescenta ao nome."""
    n = RE_HASH.sub("", nome).replace("DUPLICADO", "").strip()
    return re.sub(r"\s+", " ", n)


def limpar(dbx, pastas_teste: list[str], pasta_entrada: str, origens: dict[str, str]) -> list[str]:
    """Devolve documentos e apaga as pastas de teste. Retorna um relatório (linhas de texto)."""
    relatorio = []
    for p in pastas_teste:
        if not p.startswith(PREFIXO_TESTE):
            raise SystemExit(f"Segurança: '{p}' não é uma pasta de TESTE. Nada foi feito.")
    raizes = sorted({raiz(p) for p in pastas_teste})

    # 1) devolver documentos (cópias de teste cujo original já está no lugar ficam para ir à lixeira)
    devolvidos = []
    copias: set[str] = set()
    for r in raizes:
        for arq in dbx.listar_recursivo(r):
            if GERADOS.search(arq.caminho):
                continue
            m = RE_HASH.search(arq.nome)
            destino = origens.get(m.group(1)) if m else None
            if not destino:
                destino = f"{pasta_entrada}/{nome_original(arq.nome)}"
            if arq.content_hash and dbx.hash_de(destino) == arq.content_hash:
                # Cópia feita no modo teste: o original idêntico já está no lugar. Não devolve (evita duplicar).
                copias.add(arq.caminho)
                relatorio.append(f"cópia de teste (o original já está em {destino}): {arq.caminho}")
                continue
            final = dbx.mover(arq.caminho, destino)
            devolvidos.append(final)
            relatorio.append(f"devolvido: {arq.caminho} -> {final}")

    # 2) conferir que todos chegaram
    faltando = [d for d in devolvidos if not dbx.existe(d)]
    if faltando:
        relatorio.append("ATENÇÃO: não encontrei estes arquivos depois de devolver; nada foi apagado: "
                         + "; ".join(faltando))
        return relatorio

    # 3) apagar pastas de teste que ficaram sem documentos
    for r in raizes:
        restantes = [a.caminho for a in dbx.listar_recursivo(r)
                     if not GERADOS.search(a.caminho) and a.caminho not in copias]
        if restantes:
            relatorio.append(f"não apaguei {r}: ainda tem documentos ({len(restantes)})")
            continue
        if dbx.existe(r):
            dbx.apagar(r)
            relatorio.append(f"apagada (vai para a lixeira do Dropbox): {r}")
    return relatorio


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    from . import config as cfgmod
    from .dropbox_client import DropboxClient

    cfg = cfgmod.carregar()
    dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token,
                        cfg.dropbox_team_space)
    caminho_db = os.path.join(cfg.dados_dir, "controle.db")
    alvos = [a for a in sys.argv[1:] if a.strip()]
    if not alvos and os.environ.get("LIMPEZA_ALVO", "").strip():
        alvos = [os.environ["LIMPEZA_ALVO"].strip()]
    # Sem argumento: limpa todas as pastas de teste. Com argumento: só as pastas indicadas.
    pastas = alvos or [cfg.pasta_clientes, cfg.pasta_revisar, cfg.pasta_erros, cfg.pasta_controle]
    relatorio = limpar(dbx, pastas, cfg.pasta_entrada, origens_do_banco(caminho_db))
    if not alvos and os.path.exists(caminho_db) and not any(l.startswith("ATENÇÃO") for l in relatorio):
        novo = os.path.join(cfg.dados_dir, f"controle-teste-{datetime.now():%Y%m%d-%H%M%S}.db")
        os.rename(caminho_db, novo)
        relatorio.append(f"banco do teste guardado como {os.path.basename(novo)}")
    print("\n".join(["=" * 70, "LIMPEZA DO TESTE"] + relatorio + ["=" * 70]))


if __name__ == "__main__":
    main()
