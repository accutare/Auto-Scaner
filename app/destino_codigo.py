"""Destino por código: a equipe informa códigos (ex.: AN1000/AN1000BL/11) e o programa encontra as pastas
EXISTENTES cujo nome começa com cada código (ex.: "AN1000 - Adalberto Neto" / "AN1000BL-Bizisc Ltd" /
"11-Other"). Nunca cria pasta: se não encontrar exatamente uma, o documento vai para revisão."""
import logging
import re

log = logging.getLogger("docflow.destino")


def _candidatas(codigo: str, nomes: list[str]) -> tuple[list[str], list[str]]:
    """(com descrição, exatas): "AN1000 - Adalberto Neto" é com descrição; "AN1000" é exata."""
    padrao = re.compile(rf"^{re.escape(codigo)}(?![A-Za-z0-9])", re.IGNORECASE)
    com_descricao = [n for n in nomes if padrao.match(n) and n.lower() != codigo.lower()]
    exatas = [n for n in nomes if n.lower() == codigo.lower()]
    return com_descricao, exatas


def resolver(listar_pastas, base: str, codigos: str) -> tuple[str | None, str | None]:
    """Devolve (caminho real, None) ou (None, motivo). Prefere a pasta com descrição à pasta só com o código."""
    atual = base.rstrip("/")
    for codigo in [c.strip() for c in codigos.strip("/").split("/") if c.strip()]:
        nomes = listar_pastas(atual)
        com_descricao, exatas = _candidatas(codigo, nomes)
        escolhidas = com_descricao or exatas
        if len(escolhidas) == 1:
            atual = f"{atual}/{escolhidas[0]}"
        elif not escolhidas:
            return None, f"não encontrei pasta que comece com '{codigo}' em {atual}"
        else:
            return None, f"mais de uma pasta começa com '{codigo}' em {atual}: {', '.join(escolhidas)}"
    return atual, None


def corrigir_pastas_literais(dbx, base: str, codigos: str) -> list[str]:
    """Conserta testes antigos em que os códigos viraram pastas literais (ex.: /AN1000/AN1000BL/11):
    move os arquivos para a pasta real e manda a pasta literal (vazia) para a lixeira.
    Só age dentro de pastas de TESTE."""
    relatorio = []
    if not base.startswith("/TESTE - "):
        return relatorio
    literal = f"{base}/{codigos.strip('/')}"
    raiz_literal = f"{base}/{codigos.strip('/').split('/')[0]}"
    real, _ = resolver(dbx.listar_pastas, base, codigos)
    if not real or real == literal or not dbx.existe(literal):
        return relatorio
    for arq in dbx.listar_recursivo(literal):
        final = dbx.mover(arq.caminho, f"{real}/{arq.nome}")
        relatorio.append(f"movido para a pasta certa: {arq.caminho} -> {final}")
    if not dbx.listar_recursivo(raiz_literal):
        dbx.apagar(raiz_literal)
        relatorio.append(f"pasta criada por engano enviada para a lixeira: {raiz_literal}")
    for linha in relatorio:
        log.info(linha)
    return relatorio
