"""Leitura do documento pela IA (API do Claude): extrai os dados e escolhe cliente, serviço e categoria.

A IA recebe:
  - as regras do escritório (servicos.json),
  - o catálogo de clientes/serviços descoberto no Dropbox (app/catalogo.py),
  - o documento (PDF ou imagem).
e devolve um JSON com formato garantido (structured outputs). O catálogo vai no prompt de sistema com
cache, então só a primeira leitura de cada lote paga o catálogo inteiro.
"""
import base64
import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

log = logging.getLogger("docflow.extractor")

TIPOS = [
    "sales_invoice", "purchase_invoice", "receipt", "bank_statement", "payroll_report",
    "payslip", "hmrc_letter", "vat_return", "companies_house", "cis_statement",
    "id_document", "proof_of_address", "pension", "contract", "other",
]

INSTRUCOES = """Você trabalha na Accurate Complete Accounting Ltd, um escritório de contabilidade no Reino Unido
(com clientes também na Irlanda, no Brasil e em outros países). Os documentos chegam escaneados e
precisam ser arquivados na pasta certa do Dropbox.

No Dropbox, os clientes ficam dentro de pastas de SERVIÇO (ex.: "UK - VAT MTD Ready", "UK - Payroll").
O mesmo cliente aparece em vários serviços. Sua tarefa:
1. Ler o documento (pode estar torto, ser foto ou ter várias páginas) e extrair os dados.
2. Descobrir de QUAL cliente do escritório ele é, usando o catálogo abaixo (nome da empresa, nome comercial,
   nome do diretor/contato, Company number, VAT number, UTR). Atenção: em faturas de fornecedor o cliente é
   quem COMPRA; em faturas de venda o cliente é quem VENDE.
3. Decidir para QUAL serviço (pasta) do cliente o documento vai, seguindo as regras do escritório.
4. Se o serviço tiver categorias, escolher a categoria.

Nunca invente. Se não tiver certeza do cliente ou do serviço, use null e explique em motivo_classificacao;
o documento irá para revisão humana. "confianca" (0 a 1) deve refletir a certeza sobre o conjunto
cliente + serviço + categoria + dados lidos. Datas no formato AAAA-MM-DD (atenção: no Reino Unido
05/06/2026 é 5 de junho). valor_total como número (ex.: 1234.56), moeda como código ISO (GBP, EUR, BRL)."""


def _nulo(tipo: str) -> dict:
    return {"anyOf": [{"type": tipo}, {"type": "null"}]}


ESQUEMA = {
    "type": "object",
    "properties": {
        "legivel": {"type": "boolean"},
        "tipo_documento": {"type": "string", "enum": TIPOS},
        "resumo": {"type": "string"},
        "nome_cliente_no_documento": _nulo("string"),
        "company_number": _nulo("string"),
        "vat_number": _nulo("string"),
        "utr": _nulo("string"),
        "paye_ref": _nulo("string"),
        "cliente_id": _nulo("string"),
        "servico": _nulo("string"),
        "categoria": _nulo("string"),
        "motivo_classificacao": {"type": "string"},
        "fornecedor_ou_emissor": _nulo("string"),
        "data_documento": _nulo("string"),
        "periodo_inicio": _nulo("string"),
        "periodo_fim": _nulo("string"),
        "valor_total": _nulo("number"),
        "moeda": _nulo("string"),
        "numero_documento": _nulo("string"),
        "observacoes": _nulo("string"),
        "confianca": {"type": "number"},
    },
    "required": [
        "legivel", "tipo_documento", "resumo", "nome_cliente_no_documento", "company_number",
        "vat_number", "utr", "paye_ref", "cliente_id", "servico", "categoria",
        "motivo_classificacao", "fornecedor_ou_emissor", "data_documento", "periodo_inicio",
        "periodo_fim", "valor_total", "moeda", "numero_documento", "observacoes", "confianca",
    ],
    "additionalProperties": False,
}


@dataclass
class Extracao:
    legivel: bool = False
    tipo: str = "other"
    resumo: str = ""
    nome_cliente_no_documento: str | None = None
    company_number: str | None = None
    vat_number: str | None = None
    utr: str | None = None
    paye_ref: str | None = None
    cliente_id: str | None = None
    servico: str | None = None
    categoria: str | None = None
    motivo_classificacao: str = ""
    fornecedor_ou_emissor: str | None = None
    data_documento: str | None = None
    periodo_inicio: str | None = None
    periodo_fim: str | None = None
    valor_total: float | None = None
    moeda: str | None = None
    numero_documento: str | None = None
    observacoes: str | None = None
    confianca: float = 0.0
    recusado: bool = False
    bruto: dict = field(default_factory=dict)


def _media_type(nome: str) -> tuple[str, str]:
    n = nome.lower()
    if n.endswith(".pdf"):
        return "document", "application/pdf"
    if n.endswith((".jpg", ".jpeg")):
        return "image", "image/jpeg"
    if n.endswith(".png"):
        return "image", "image/png"
    if n.endswith(".webp"):
        return "image", "image/webp"
    raise ValueError(f"Formato não suportado: {nome}")


def _num(v):
    """Aceita número ou texto em formato UK (1,234.56) ou europeu/BR (1.234,56)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        inteiro, _, dec = s.rpartition(",")
        s = s.replace(",", "") if len(dec) == 3 else f"{inteiro.replace(',', '')}.{dec}"
    try:
        return float(s)
    except ValueError:
        return None


def _data(v):
    v = (v or "").strip() if isinstance(v, str) else None
    return v if v and re.match(r"^\d{4}-\d{2}-\d{2}$", v) else None


def interpretar_resposta(texto: str) -> Extracao:
    """Converte o JSON devolvido pela IA em Extracao (tolerante a cercas ```json)."""
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        raise ValueError("A IA não devolveu JSON")
    d = json.loads(m.group(0))
    tipo = d.get("tipo_documento") or "other"
    try:
        conf = float(d.get("confianca") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return Extracao(
        legivel=bool(d.get("legivel", False)),
        tipo=tipo if tipo in TIPOS else "other",
        resumo=d.get("resumo") or "",
        nome_cliente_no_documento=d.get("nome_cliente_no_documento"),
        company_number=d.get("company_number"),
        vat_number=d.get("vat_number"),
        utr=d.get("utr"),
        paye_ref=d.get("paye_ref"),
        cliente_id=(d.get("cliente_id") or None),
        servico=(d.get("servico") or None),
        categoria=(d.get("categoria") or None),
        motivo_classificacao=d.get("motivo_classificacao") or "",
        fornecedor_ou_emissor=d.get("fornecedor_ou_emissor"),
        data_documento=_data(d.get("data_documento")),
        periodo_inicio=_data(d.get("periodo_inicio")),
        periodo_fim=_data(d.get("periodo_fim")),
        valor_total=_num(d.get("valor_total")),
        moeda=(d.get("moeda") or None),
        numero_documento=d.get("numero_documento"),
        observacoes=d.get("observacoes"),
        confianca=max(0.0, min(1.0, conf)),
        bruto=d,
    )


def montar_sistema(texto_regras: list[str], texto_servicos: str, texto_catalogo: str) -> str:
    regras = "\n".join(f"- {r}" for r in texto_regras)
    return (
        f"{INSTRUCOES}\n\n## Regras do escritório\n{regras}\n\n"
        f"## Serviços (use o nome exato da pasta em \"servico\")\n{texto_servicos}\n\n"
        f"## Catálogo de clientes (use o código, ex.: C0001, em \"cliente_id\")\n{texto_catalogo}\n"
    )


class Extrator:
    def __init__(self, api_key: str, modelo: str, esforco: str = "medium"):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.modelo = modelo
        self.esforco = esforco

    def extrair(self, nome: str, conteudo: bytes, sistema: str) -> Extracao:
        bloco_tipo, media = _media_type(nome)
        b64 = base64.standard_b64encode(conteudo).decode()
        bloco = {"type": bloco_tipo, "source": {"type": "base64", "media_type": media, "data": b64}}
        resp = self.client.beta.messages.create(
            model=self.modelo,
            max_tokens=16000,
            # Se o filtro de segurança recusar, a própria API tenta de novo com o modelo indicado por ela
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": self.esforco, "format": {"type": "json_schema", "schema": ESQUEMA}},
            messages=[{
                "role": "user",
                "content": [bloco, {"type": "text", "text": f"Arquivo: {nome}. Classifique este documento."}],
            }],
        )
        u = resp.usage
        log.info("IA: entrada=%s cache_lido=%s saída=%s", u.input_tokens,
                 getattr(u, "cache_read_input_tokens", None), u.output_tokens)
        if resp.stop_reason == "refusal":
            return Extracao(recusado=True, motivo_classificacao="a IA recusou ler o documento")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("resposta da IA cortada (max_tokens)")
        texto = "".join(b.text for b in resp.content if b.type == "text")
        return interpretar_resposta(texto)
