"""Decide o destino do documento: valida a resposta da IA e monta o caminho na estrutura por cliente.

Destino (OK):  <Pasta de clientes>/<País>/<Cliente>/<PREFIXO> - <Serviço>/[subpasta]/<nome original do arquivo>
  ex.: /TESTE - Clientes/Accurate UK/ARS Car Direct Ltd/UK - Limited Company/YE 31 03 2027/
       ARS Car Direct - DVLA VTL306 09.11.2026 (1).pdf

Todo cliente recebe todas as pastas de serviço (mesmo vazias), para a equipe achar tudo no mesmo lugar.
O nome da pasta do cliente é sempre o mesmo para o mesmo cliente: se ele já existe no Client Data de hoje,
usa aquele nome; se já existe uma pasta dele na estrutura nova, reaproveita a pasta. Em qualquer dúvida, o
documento vai para a pasta de revisão com o motivo (e a sugestão da IA fica no Controle.xlsx).
"""
import calendar
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from .catalogo import Catalogo, Cliente, chave_cliente
from .extractor import Extracao
from .servicos import Regras

PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def limpar_nome(texto: str, limite: int = 60) -> str:
    """Nome seguro para arquivo de revisão, mantendo espaços."""
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
    pastas_criar: list[str] = field(default_factory=list)  # pastas de serviço do cliente (criadas vazias)


def data_referencia(ext: Extracao) -> date | None:
    for v in (ext.periodo_fim, ext.data_documento):
        if v:
            try:
                return date.fromisoformat(v)
            except ValueError:
                pass
    return None


def year_end(ref: date, dia_mes: str) -> date:
    """Primeiro fim de ano fiscal (dia/mês) na data ou depois dela. Ex.: 09/11/2026 com 31/03 -> 31/03/2027."""
    dia, mes = (int(x) for x in re.split(r"[/.\- ]", dia_mes.strip())[:2])
    for ano in (ref.year, ref.year + 1):
        ultimo = calendar.monthrange(ano, mes)[1]
        d = date(ano, mes, min(dia, ultimo))
        if d >= ref:
            return d
    return date(ref.year + 1, mes, min(dia, calendar.monthrange(ref.year + 1, mes)[1]))


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


def nome_revisao(ext: Extracao, nome_original: str, hash_curto: str) -> str:
    base, extensao = os.path.splitext(nome_original)
    return f"[{hash_curto}] {limpar_nome(base, 100)}{extensao.lower() or '.pdf'}"


def decidir(ext: Extracao, nome_original: str, cat: Catalogo, regras: Regras, confianca_minima: float,
            pasta_clientes: str, pasta_revisar: str, hash_curto: str, listar_pastas,
            hoje: date | None = None) -> Decisao:
    cliente_atual, conflito = identificar_cliente(ext, cat)
    pais = regras.paises.get(ext.pais or "")
    servico = regras.servico(ext.servico)

    motivos = []
    if ext.recusado:
        motivos.append("a IA recusou ler o documento")
    if not ext.legivel:
        motivos.append("documento ilegível")
    if ext.confianca < confianca_minima:
        motivos.append(f"confiança baixa ({ext.confianca:.2f})")
    if conflito:
        motivos.append(conflito)
    if not pais:
        motivos.append("país não identificado")
    nome_cli = nome_pasta(cliente_atual.nome if cliente_atual else (ext.nome_cliente or ""))
    if not nome_cli:
        motivos.append("cliente não identificado")
    if not servico:
        motivos.append("serviço não identificado")

    if motivos:
        if ext.motivo_classificacao:
            motivos.append(f"IA: {ext.motivo_classificacao}")
        return Decisao("PENDENTE", f"{pasta_revisar}/{nome_revisao(ext, nome_original, hash_curto)}",
                       nome_cli or None, ext.servico, ext.tipo_documento, ext.pais, "; ".join(motivos))

    avisos = []
    # Reaproveita a pasta do cliente se ela já existe na estrutura nova (mesmo nome escrito diferente)
    base_pais = f"{pasta_clientes}/{pais.pasta}"
    chave = chave_cliente(nome_cli)
    existente = next((p for p in listar_pastas(base_pais) if chave_cliente(p) == chave), None)
    if existente:
        nome_cli = existente
    elif not cliente_atual:
        avisos.append("cliente novo (não está no Client Data de hoje): confira o nome da pasta")

    pasta_cliente = f"{base_pais}/{nome_cli}"
    pasta_servico = f"{pasta_cliente}/{pais.prefixo} - {servico.pasta}"
    destino = pasta_servico
    if servico.subpasta:
        ref = data_referencia(ext)
        if not ref:
            ref = hoje or date.today()
            avisos.append("documento sem data: ano fiscal calculado pela data de hoje")
        dia_mes = (cliente_atual.identificadores.get("year_end") if cliente_atual else None) or regras.year_end_padrao
        ye = year_end(ref, dia_mes)
        destino = f"{pasta_servico}/{servico.subpasta.replace('{ye}', ye.strftime('%d %m %Y'))}"

    pastas = [f"{pasta_cliente}/{p}" for p in regras.pastas_de_servico(pais, ext.tipo_cliente)]
    return Decisao("OK", f"{destino}/{nome_original}", nome_cli, servico.pasta, ext.tipo_documento,
                   ext.pais, "; ".join(avisos) or None, pastas)
