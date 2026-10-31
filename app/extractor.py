"""Leitura do documento pela IA (API do Claude) com saída estruturada em JSON."""
import base64
import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

log = logging.getLogger("docflow.extractor")

TIPOS = [
    "nota_fiscal", "recibo", "comprovante_pagamento", "boleto",
    "extrato_bancario", "guia_imposto", "contrato", "folha_pagamento",
    "documento_pessoal", "outro",
]

PROMPT = f"""Você é um assistente de escritório de contabilidade no Brasil.
Leia o documento anexo (pode ser escaneado, torto ou foto) e extraia os dados.

Responda APENAS com um objeto JSON, sem texto antes ou depois, neste formato:
{{
  "tipo": um de {TIPOS},
  "cnpj_cpf_cliente": "somente dígitos do CNPJ/CPF do CLIENTE do escritório (quem contrata a contabilidade), ou null",
  "nome_cliente": "nome do cliente, ou null",
  "fornecedor_ou_emissor": "nome de quem emitiu/recebeu o pagamento, ou null",
  "cnpj_cpf_fornecedor": "somente dígitos, ou null",
  "data_documento": "AAAA-MM-DD, ou null",
  "competencia": "AAAA-MM, ou null",
  "valor_total": número com ponto decimal (ex: 1234.56), ou null,
  "numero_documento": "número da nota/boleto/guia, ou null",
  "chave_acesso": "44 dígitos da NF-e se houver, ou null",
  "observacoes": "algo importante que o contador deve saber, ou null",
  "confianca": número de 0 a 1 indicando sua confiança geral na leitura,
  "legivel": true ou false
}}

Regras:
- Nunca invente valores. Se não estiver visível ou legível, use null e reduza a confiança.
- Em nota fiscal, o cliente do escritório costuma ser o TOMADOR (quem compra) ou o PRESTADOR (quem vende); informe os dois CNPJs que aparecerem em observacoes se não tiver certeza.
- Valores brasileiros usam vírgula decimal e ponto de milhar: converta corretamente.
"""


@dataclass
class Extracao:
    tipo: str = "outro"
    cnpj_cpf_cliente: str | None = None
    nome_cliente: str | None = None
    fornecedor_ou_emissor: str | None = None
    cnpj_cpf_fornecedor: str | None = None
    data_documento: str | None = None
    competencia: str | None = None
    valor_total: float | None = None
    numero_documento: str | None = None
    chave_acesso: str | None = None
    observacoes: str | None = None
    confianca: float = 0.0
    legivel: bool = False
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


def _somente_digitos(v):
    if v is None:
        return None
    d = re.sub(r"\D", "", str(v))
    return d or None


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def interpretar_resposta(texto: str) -> Extracao:
    """Converte o texto devolvido pela IA em Extracao (tolerante a cercas ```json)."""
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        raise ValueError("A IA não devolveu JSON")
    dados = json.loads(m.group(0))
    tipo = dados.get("tipo") or "outro"
    if tipo not in TIPOS:
        tipo = "outro"
    try:
        conf = float(dados.get("confianca") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return Extracao(
        tipo=tipo,
        cnpj_cpf_cliente=_somente_digitos(dados.get("cnpj_cpf_cliente")),
        nome_cliente=dados.get("nome_cliente"),
        fornecedor_ou_emissor=dados.get("fornecedor_ou_emissor"),
        cnpj_cpf_fornecedor=_somente_digitos(dados.get("cnpj_cpf_fornecedor")),
        data_documento=dados.get("data_documento"),
        competencia=dados.get("competencia"),
        valor_total=_num(dados.get("valor_total")),
        numero_documento=dados.get("numero_documento"),
        chave_acesso=_somente_digitos(dados.get("chave_acesso")),
        observacoes=dados.get("observacoes"),
        confianca=max(0.0, min(1.0, conf)),
        legivel=bool(dados.get("legivel", False)),
        bruto=dados,
    )


class Extrator:
    def __init__(self, api_key: str, modelo: str):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.modelo = modelo

    def extrair(self, nome: str, conteudo: bytes) -> Extracao:
        bloco_tipo, media = _media_type(nome)
        b64 = base64.standard_b64encode(conteudo).decode()
        bloco = {
            "type": bloco_tipo,
            "source": {"type": "base64", "media_type": media, "data": b64},
        }
        resp = self.client.messages.create(
            model=self.modelo,
            max_tokens=1200,
            temperature=0,
            messages=[{"role": "user", "content": [bloco, {"type": "text", "text": PROMPT}]}],
        )
        texto = "".join(b.text for b in resp.content if b.type == "text")
        return interpretar_resposta(texto)
