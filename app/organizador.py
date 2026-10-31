"""Regras de organização: identificar cliente, montar nomes e decidir o destino."""
import csv
import os
import re
import unicodedata
from dataclasses import dataclass

from .extractor import Extracao


def _limpar_nome(texto: str, limite: int = 40) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    texto = re.sub(r"[^A-Za-z0-9 _-]", "", texto).strip()
    texto = re.sub(r"\s+", "_", texto)
    return texto[:limite] or "sem_nome"


def carregar_clientes(caminho_csv: str) -> dict[str, str]:
    """Lê clientes.csv (colunas: cnpj_cpf;nome) e devolve {digitos: nome}."""
    clientes: dict[str, str] = {}
    if not os.path.exists(caminho_csv):
        return clientes
    with open(caminho_csv, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(2048)
        f.seek(0)
        delim = ";" if amostra.count(";") >= amostra.count(",") else ","
        for linha in csv.DictReader(f, delimiter=delim):
            doc = re.sub(r"\D", "", linha.get("cnpj_cpf", "") or "")
            nome = (linha.get("nome") or "").strip()
            if doc and nome:
                clientes[doc] = nome
    return clientes


@dataclass
class Decisao:
    status: str                 # OK | PENDENTE | ERRO
    pasta_destino: str          # pasta base (processados / revisar / erros)
    caminho_destino: str        # caminho completo do arquivo
    cliente: str | None
    motivo: str | None


def identificar_cliente(ext: Extracao, clientes: dict[str, str]) -> tuple[str | None, str | None]:
    """Devolve (documento_do_cliente, nome). Tenta o CNPJ lido; depois o do fornecedor
    (caso a IA tenha trocado os papéis); depois qualquer CNPJ citado nas observações."""
    candidatos = [ext.cnpj_cpf_cliente, ext.cnpj_cpf_fornecedor]
    candidatos += re.findall(r"\d{11,14}", re.sub(r"[.\-/]", "", ext.observacoes or ""))
    for doc in candidatos:
        if doc and doc in clientes:
            return doc, clientes[doc]
    return None, None


def decidir(
    ext: Extracao,
    nome_original: str,
    clientes: dict[str, str],
    confianca_minima: float,
    pastas: dict[str, str],
    hash_curto: str,
) -> Decisao:
    extensao = os.path.splitext(nome_original)[1].lower() or ".pdf"
    doc, nome_cliente = identificar_cliente(ext, clientes)

    motivos = []
    if not ext.legivel:
        motivos.append("documento ilegível")
    if ext.confianca < confianca_minima:
        motivos.append(f"confiança baixa ({ext.confianca:.2f})")
    if not nome_cliente:
        motivos.append("cliente não identificado na lista de clientes")
    if ext.valor_total is None and ext.tipo not in ("documento_pessoal", "contrato", "outro"):
        motivos.append("valor não identificado")
    if not ext.data_documento:
        motivos.append("data não identificada")

    if motivos:
        nome_final = f"{hash_curto}_{_limpar_nome(os.path.splitext(nome_original)[0], 60)}{extensao}"
        return Decisao(
            "PENDENTE", pastas["revisar"], f"{pastas['revisar']}/{nome_final}",
            nome_cliente, "; ".join(motivos),
        )

    ano, mes = (ext.data_documento or "0000-00")[:4], (ext.data_documento or "0000-00")[5:7]
    if ext.competencia and re.match(r"^\d{4}-\d{2}$", ext.competencia):
        ano, mes = ext.competencia[:4], ext.competencia[5:7]
    pasta = f"{pastas['processados']}/{_limpar_nome(nome_cliente, 60)}/{ano}/{mes}/{ext.tipo}"
    forn = _limpar_nome(ext.fornecedor_ou_emissor or "", 30)
    valor = f"{ext.valor_total:.2f}".replace(".", ",") if ext.valor_total is not None else "sem_valor"
    nome_final = f"{ext.data_documento}_{ext.tipo}_{forn}_{valor}_{hash_curto}{extensao}"
    return Decisao("OK", pasta, f"{pasta}/{nome_final}", nome_cliente, None)
