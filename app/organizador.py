"""Decide o destino do documento: valida a resposta da IA e monta o caminho na estrutura por cliente.

Destino (OK):  <Pasta de clientes>/<País>/<Cliente>/<Serviço>/<Tipo de documento>/<nome padronizado>
  ex.: /Clientes - Teste/Accurate UK/Alves Marketing Business and Network Ltd/VAT/Purchase invoices/
       2026-05-14 - Purchase invoice - Smart Growth Consultancy Ltd - 1206 - GBP 250.00 [a1b2c3d4].pdf

O nome da pasta do cliente é sempre o mesmo para o mesmo cliente: se ele já existe no Client Data de hoje,
usa aquele nome; se já existe uma pasta dele na estrutura nova, reaproveita a pasta. Em qualquer dúvida, o
documento vai para a pasta de revisão com o motivo (e a sugestão da IA fica no Controle.xlsx).
"""
import os
import re
import unicodedata
from dataclasses import dataclass

from .catalogo import Catalogo, Cliente, chave_cliente
from .extractor import Extracao
from .servicos import Regras

PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def limpar_nome(texto: str, limite: int = 60) -> str:
    """Nome seguro para arquivo, mantendo espaços (como a equipe já nomeia no Dropbox)."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9 &._()-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:limite].strip() or "sem nome"


def nome_pasta(texto: str, limite: int = 120) -> str:
    """Nome de pasta: mantém acentos e pontuação comum, tira só o que o Dropbox não aceita."""
    t = PROIBIDOS.sub(" ", texto or "")
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:limite].strip(" .")


@dataclass
class Decisao:
    status: str                 # OK | PENDENTE
    caminho_destino: str
    cliente: str | None
    servico: str | None
    tipo: str | None
    pais: str | None
    motivo: str | None          # se PENDENTE: por quê; se OK: avisos (ex.: cliente novo)


def nome_arquivo(ext: Extracao, rotulo: str, extensao: str, hash_curto: str) -> str:
    partes = [ext.data_documento or "sem data", rotulo]
    if ext.fornecedor_ou_emissor:
        partes.append(limpar_nome(ext.fornecedor_ou_emissor, 40))
    if ext.numero_documento:
        partes.append(limpar_nome(str(ext.numero_documento), 20))
    if ext.valor_total is not None:
        partes.append(f"{ext.moeda or ''} {ext.valor_total:.2f}".strip())
    return f"{' - '.join(partes)} [{hash_curto}]{extensao}"


def identificar_cliente(ext: Extracao, cat: Catalogo) -> tuple[Cliente | None, str | None]:
    """Cliente da lista atual escolhido pela IA, conferido com os números (Company/VAT/UTR/PAYE)."""
    escolhido = cat.por_id(ext.cliente_id)
    por_numero = None
    for tipo in ("company_number", "vat_number", "utr", "paye_ref"):
        por_numero = cat.por_identificador(tipo, getattr(ext, tipo))
        if por_numero:
            break
    if escolhido and por_numero and escolhido.id != por_numero.id:
        return None, (f"conflito: a IA escolheu {escolhido.nome}, mas o número no documento é de "
                      f"{por_numero.nome}")
    return escolhido or por_numero, None


def decidir(ext: Extracao, nome_original: str, cat: Catalogo, regras: Regras, confianca_minima: float,
            pasta_clientes: str, pasta_revisar: str, hash_curto: str, listar_pastas) -> Decisao:
    extensao = os.path.splitext(nome_original)[1].lower() or ".pdf"
    cliente_atual, conflito = identificar_cliente(ext, cat)
    pasta_pais = regras.paises.get(ext.pais or "")

    motivos = []
    if ext.recusado:
        motivos.append("a IA recusou ler o documento")
    if not ext.legivel:
        motivos.append("documento ilegível")
    if ext.confianca < confianca_minima:
        motivos.append(f"confiança baixa ({ext.confianca:.2f})")
    if conflito:
        motivos.append(conflito)
    if not pasta_pais:
        motivos.append("país não identificado")
    nome_cli = nome_pasta(cliente_atual.nome if cliente_atual else (ext.nome_cliente or ""))
    if not nome_cli:
        motivos.append("cliente não identificado")
    if ext.servico not in regras.nomes_servicos:
        motivos.append("serviço não identificado")
    if ext.tipo_documento not in regras.nomes_tipos:
        motivos.append("tipo de documento não identificado")

    rotulo = regras.rotulo_tipo(ext.tipo_documento)
    arquivo = nome_arquivo(ext, rotulo, extensao, hash_curto)
    if motivos:
        if ext.motivo_classificacao:
            motivos.append(f"IA: {ext.motivo_classificacao}")
        return Decisao("PENDENTE", f"{pasta_revisar}/{arquivo}", nome_cli or None, ext.servico,
                       ext.tipo_documento, ext.pais, "; ".join(motivos))

    # Reaproveita a pasta do cliente se ela já existe na estrutura nova (mesmo nome escrito diferente)
    base_pais = f"{pasta_clientes}/{pasta_pais}"
    chave = chave_cliente(nome_cli)
    for existente in listar_pastas(base_pais):
        if chave_cliente(existente) == chave:
            nome_cli = existente
            break
    else:
        if not cliente_atual:
            aviso = "cliente novo (não está no Client Data de hoje): confira o nome da pasta"
            return Decisao("OK", f"{base_pais}/{nome_cli}/{ext.servico}/{ext.tipo_documento}/{arquivo}",
                           nome_cli, ext.servico, ext.tipo_documento, ext.pais, aviso)
    return Decisao("OK", f"{base_pais}/{nome_cli}/{ext.servico}/{ext.tipo_documento}/{arquivo}",
                   nome_cli, ext.servico, ext.tipo_documento, ext.pais, None)
