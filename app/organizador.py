"""Decide o destino do documento: valida a escolha da IA e monta o caminho dentro da pasta do cliente.

Destino (OK):  <Client Data>/<Serviço>/<Pasta do cliente>/<subpasta do serviço>/<nome padronizado>
  ex.: .../UK - VAT MTD Ready/Alves Marketing ... TA FGV/VAT's/VAT Qe 30.06.2026/Suppliers/2026-05-14 ...pdf
Em qualquer dúvida, vai para a pasta de revisão com o motivo (e a sugestão da IA fica no Controle.xlsx).
O programa nunca cria pasta de cliente nem de serviço: só subpastas dentro de uma pasta de cliente existente.
"""
import calendar
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from .catalogo import Catalogo, Cliente
from .extractor import Extracao
from .servicos import Regras

NOMES_TIPO = {
    "sales_invoice": "Sales invoice", "purchase_invoice": "Purchase invoice", "receipt": "Receipt",
    "bank_statement": "Bank statement", "payroll_report": "Payroll report", "payslip": "Payslip",
    "hmrc_letter": "HMRC letter", "vat_return": "VAT return", "companies_house": "Companies House",
    "cis_statement": "CIS statement", "id_document": "ID document",
    "proof_of_address": "Proof of address", "pension": "Pension", "contract": "Contract",
    "other": "Document",
}

RE_QE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def limpar_nome(texto: str, limite: int = 60) -> str:
    """Nome seguro para arquivo, mantendo espaços (como a equipe já nomeia no Dropbox)."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9 &._()-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:limite].strip() or "sem nome"


@dataclass
class Decisao:
    status: str                 # OK | PENDENTE
    caminho_destino: str
    cliente: str | None
    servico: str | None
    categoria: str | None
    motivo: str | None


def _fim_do_mes(ano: int, mes: int) -> date:
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def escolher_trimestre_vat(pastas_existentes: list[str], prefixo: str, data_ref: date) -> str | None:
    """Escolhe o nome da pasta "VAT Qe dd.mm.aaaa" do trimestre que contém data_ref.

    O ciclo de trimestres do cliente (stagger) é deduzido das pastas que ele já tem. Se a pasta do
    trimestre já existe, devolve o nome exato dela; se não, devolve o nome novo no mesmo formato.
    Sem nenhuma pasta anterior não dá para saber o ciclo: devolve None (vai para revisão)."""
    existentes: dict[date, str] = {}
    for nome in pastas_existentes:
        if not nome.lower().startswith(prefixo.lower()):
            continue
        m = RE_QE.search(nome[len(prefixo):])
        if m:
            try:
                existentes[date(int(m.group(3)), int(m.group(2)), int(m.group(1)))] = nome
            except ValueError:
                continue
    if not existentes:
        return None
    mes_base = next(iter(existentes)).month
    # primeiro fim de mês, a partir do mês de data_ref, que cai no ciclo do cliente
    ano, mes = data_ref.year, data_ref.month
    while (mes - mes_base) % 3 != 0:
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    alvo = _fim_do_mes(ano, mes)
    for d, nome in existentes.items():
        if d == alvo or (d.year == alvo.year and d.month == alvo.month):
            return nome
    return f"{prefixo}{alvo:%d.%m.%Y}"


def _data_ref(ext: Extracao) -> date | None:
    for v in (ext.periodo_fim, ext.data_documento):
        if v:
            try:
                return date.fromisoformat(v)
            except ValueError:
                pass
    return None


def montar_subpasta(modelo: str, pasta_cliente: str, ext: Extracao, categoria: str | None,
                    listar_pastas) -> tuple[str | None, str | None]:
    """Resolve as variáveis do modelo de subpasta. Devolve (subpasta, motivo_se_falhou)."""
    d = _data_ref(ext)
    sub = modelo
    if "{vat_qe}" in sub:
        if not d:
            return None, "sem data para escolher o trimestre de VAT"
        antes = sub.split("{vat_qe}")[0]                 # "VAT's/VAT Qe "
        pai, _, prefixo = antes.rpartition("/")          # "VAT's", "VAT Qe "
        base = f"{pasta_cliente}/{pai}" if pai else pasta_cliente
        nome_qe = escolher_trimestre_vat(listar_pastas(base), prefixo, d)
        if not nome_qe:
            return None, f"cliente sem pastas '{prefixo.strip()}' em '{pai}' para deduzir o trimestre de VAT"
        sub = (f"{pai}/" if pai else "") + nome_qe + sub.split("{vat_qe}", 1)[1]
    if "{categoria}" in sub:
        if not categoria:
            return None, "categoria não identificada"
        sub = sub.replace("{categoria}", categoria)
    if "{ano}" in sub or "{mes}" in sub:
        if not d:
            return None, "sem data para montar a pasta"
        sub = sub.replace("{ano}", f"{d.year}").replace("{mes}", f"{d.month:02d}")
    return sub.strip("/"), None


def nome_arquivo(ext: Extracao, extensao: str, hash_curto: str) -> str:
    partes = [ext.data_documento or "sem data", NOMES_TIPO.get(ext.tipo, "Document")]
    if ext.fornecedor_ou_emissor:
        partes.append(limpar_nome(ext.fornecedor_ou_emissor, 40))
    if ext.numero_documento:
        partes.append(limpar_nome(str(ext.numero_documento), 20))
    if ext.valor_total is not None:
        partes.append(f"{ext.moeda or ''} {ext.valor_total:.2f}".strip())
    return f"{' - '.join(partes)} [{hash_curto}]{extensao}"


def identificar_cliente(ext: Extracao, cat: Catalogo) -> tuple[Cliente | None, str | None]:
    """Cliente escolhido pela IA, conferido com os números (Company/VAT/UTR/PAYE) do clientes_extra.csv."""
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


def decidir(ext: Extracao, nome_original: str, cat: Catalogo, regras: Regras,
            confianca_minima: float, pasta_revisar: str, hash_curto: str, listar_pastas) -> Decisao:
    extensao = os.path.splitext(nome_original)[1].lower() or ".pdf"
    cliente, conflito = identificar_cliente(ext, cat)
    servico = ext.servico
    categoria = ext.categoria

    motivos = []
    if ext.recusado:
        motivos.append("a IA recusou ler o documento")
    if not ext.legivel:
        motivos.append("documento ilegível")
    if ext.confianca < confianca_minima:
        motivos.append(f"confiança baixa ({ext.confianca:.2f})")
    if conflito:
        motivos.append(conflito)
    elif not cliente:
        motivos.append("cliente não identificado")
    if not servico:
        motivos.append("serviço não definido")
    elif cliente and servico not in cliente.pastas:
        motivos.append(f"o cliente não tem pasta no serviço '{servico}'")
    cats = regras.categorias(servico or "")
    if categoria and cats and categoria not in cats:
        categoria = None

    sub = None
    if not motivos:
        pasta_cliente = cliente.pastas[servico]
        sub, erro = montar_subpasta(regras.subpasta(servico), pasta_cliente, ext, categoria, listar_pastas)
        if erro:
            motivos.append(erro)

    nome_final = nome_arquivo(ext, extensao, hash_curto)
    if motivos:
        if ext.motivo_classificacao:
            motivos.append(f"IA: {ext.motivo_classificacao}")
        return Decisao("PENDENTE", f"{pasta_revisar}/{nome_final}",
                       cliente.nome if cliente else None, servico, categoria, "; ".join(motivos))
    destino = f"{cliente.pastas[servico]}/{sub}/{nome_final}" if sub else f"{cliente.pastas[servico]}/{nome_final}"
    return Decisao("OK", destino, cliente.nome, servico, categoria, None)
