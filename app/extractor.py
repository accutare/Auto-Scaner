"""Leitura do documento pela IA (API do Claude): extrai os dados e decide país, cliente, serviço e tipo.

A IA recebe:
  - as regras do escritório (servicos.json): países, serviços e tipos de documento,
  - a lista de clientes que já existem hoje no Dropbox (app/catalogo.py), para reconhecer nomes,
  - o documento (PDF ou imagem).
e devolve um JSON com formato garantido (structured outputs). A lista vai no prompt de sistema com
cache, então só a primeira leitura de cada lote paga a lista inteira.
Se o serviço recusar o formato do pedido (erro 400), tenta automaticamente formas mais simples.
"""
import base64
import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

log = logging.getLogger("docflow.extractor")

INSTRUCOES = """Você trabalha na Accurate Complete Accounting Ltd, um escritório de contabilidade no Reino Unido
(com clientes também no Brasil, na Irlanda e em Portugal). Os documentos chegam escaneados e o programa
vai arquivá-los no Dropbox assim: <País> / <Cliente> / <Serviço> / <Tipo de documento> / arquivo.

Sua tarefa, para cada documento:
1. Ler o documento (pode estar torto, ser foto ou ter várias páginas) e extrair os dados.
2. Descobrir o CLIENTE do escritório (empresa ou pessoa), o DONO/DIRETOR se aparecer, e o PAÍS.
   Se o cliente estiver na lista de clientes atuais abaixo, informe o código dele em "cliente_id" e use o
   mesmo nome. Se não estiver, deixe cliente_id null e escreva o nome oficial em "nome_cliente".
3. Escolher o SERVIÇO e o TIPO DE DOCUMENTO, seguindo as regras do escritório.

Nunca invente. Se não tiver certeza do cliente, do país ou do serviço, use null e explique em
motivo_classificacao: o documento irá para revisão humana. "confianca" (0 a 1) deve refletir a certeza
sobre o conjunto cliente + país + serviço + tipo + dados lidos. Datas no formato AAAA-MM-DD (no Reino
Unido 05/06/2026 é 5 de junho). valor_total como número (ex.: 1234.56), moeda como código ISO (GBP, EUR, BRL)."""


def montar_esquema(paises: list[str], servicos: list[str], tipos: list[str]) -> dict:
    """Formato da resposta. Mantido simples de propósito (sem campos opcionais nem "anyOf"):
    texto vazio "" quer dizer "não sei / não aparece no documento"."""
    texto = {"type": "string"}
    props = {
        "legivel": {"type": "boolean"},
        "resumo": texto,
        "pais": {"type": "string", "enum": list(paises) + [""]},
        "tipo_cliente": {"type": "string", "enum": ["empresa", "pessoa"]},
        "cliente_id": texto,
        "nome_cliente": texto,
        "dono_ou_diretor": texto,
        "company_number": texto,
        "vat_number": texto,
        "utr": texto,
        "paye_ref": texto,
        "servico": {"type": "string", "enum": list(servicos) + [""]},
        "tipo_documento": {"type": "string", "enum": list(tipos) + [""]},
        "motivo_classificacao": texto,
        "fornecedor_ou_emissor": texto,
        "data_documento": texto,
        "periodo_inicio": texto,
        "periodo_fim": texto,
        "valor_total": texto,
        "moeda": texto,
        "numero_documento": texto,
        "observacoes": texto,
        "confianca": {"type": "number"},
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def instrucao_formato(esquema: dict) -> str:
    """Usada no plano B (sem formato garantido): descreve o JSON esperado no próprio texto."""
    linhas = []
    for k, v in esquema["properties"].items():
        if "enum" in v:
            linhas.append(f'  "{k}": um de {json.dumps(v["enum"], ensure_ascii=False)}')
        else:
            linhas.append(f'  "{k}": {v["type"]}')
    return ("Responda APENAS com um objeto JSON, sem texto antes ou depois, com exatamente estes campos "
            "(use \"\" quando não souber):\n{\n" + ",\n".join(linhas) + "\n}")


@dataclass
class Extracao:
    legivel: bool = False
    resumo: str = ""
    pais: str | None = None
    tipo_cliente: str = "empresa"
    cliente_id: str | None = None
    nome_cliente: str | None = None
    dono_ou_diretor: str | None = None
    company_number: str | None = None
    vat_number: str | None = None
    utr: str | None = None
    paye_ref: str | None = None
    servico: str | None = None
    tipo_documento: str | None = None
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


def _texto(v):
    v = (v or "").strip() if isinstance(v, str) else None
    return v or None


def interpretar_resposta(texto: str) -> Extracao:
    """Converte o JSON devolvido pela IA em Extracao (tolerante a cercas ```json)."""
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        raise ValueError("A IA não devolveu JSON")
    d = json.loads(m.group(0))
    try:
        conf = float(d.get("confianca") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return Extracao(
        legivel=bool(d.get("legivel", False)),
        resumo=d.get("resumo") or "",
        pais=_texto(d.get("pais")),
        tipo_cliente="pessoa" if d.get("tipo_cliente") == "pessoa" else "empresa",
        cliente_id=_texto(d.get("cliente_id")),
        nome_cliente=_texto(d.get("nome_cliente")),
        dono_ou_diretor=_texto(d.get("dono_ou_diretor")),
        company_number=_texto(d.get("company_number")),
        vat_number=_texto(d.get("vat_number")),
        utr=_texto(d.get("utr")),
        paye_ref=_texto(d.get("paye_ref")),
        servico=_texto(d.get("servico")),
        tipo_documento=_texto(d.get("tipo_documento")),
        motivo_classificacao=d.get("motivo_classificacao") or "",
        fornecedor_ou_emissor=_texto(d.get("fornecedor_ou_emissor")),
        data_documento=_data(d.get("data_documento")),
        periodo_inicio=_data(d.get("periodo_inicio")),
        periodo_fim=_data(d.get("periodo_fim")),
        valor_total=_num(d.get("valor_total")),
        moeda=_texto(d.get("moeda")),
        numero_documento=_texto(d.get("numero_documento")),
        observacoes=_texto(d.get("observacoes")),
        confianca=max(0.0, min(1.0, conf)),
        bruto=d,
    )


def montar_sistema(regras, texto_catalogo: str) -> str:
    gerais = "\n".join(f"- {r}" for r in regras.regras_gerais)
    return (
        f"{INSTRUCOES}\n\n## Regras do escritório\n{gerais}\n\n"
        f"## Opções\n{regras.texto_para_ia()}\n\n"
        f"## Clientes atuais do escritório (código | nome | contatos | serviços que contratam hoje)\n"
        f"{texto_catalogo}\n"
    )


class Extrator:
    def __init__(self, api_key: str, modelo: str, esforco: str = "medium"):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.modelo = modelo
        self.esforco = esforco

    def extrair(self, nome: str, conteudo: bytes, sistema: str, esquema: dict) -> Extracao:
        bloco_tipo, media = _media_type(nome)
        b64 = base64.standard_b64encode(conteudo).decode()
        bloco = {"type": bloco_tipo, "source": {"type": "base64", "media_type": media, "data": b64}}
        pedido = f"Arquivo: {nome}. Classifique este documento."

        # Plano A: formato de resposta garantido. Se o serviço recusar o pedido (erro 400),
        # tenta formas mais simples antes de desistir, e guarda a mensagem completa de cada recusa.
        tentativas = [
            ("formato+esforco", {"output_config": {"effort": self.esforco,
                                                   "format": {"type": "json_schema", "schema": esquema}}}, pedido),
            ("formato", {"output_config": {"format": {"type": "json_schema", "schema": esquema}}}, pedido),
            ("texto", {}, pedido + "\n\n" + instrucao_formato(esquema)),
        ]
        recusas = []
        for modo, extra, texto_pedido in tentativas:
            try:
                resp = self.client.messages.create(
                    model=self.modelo,
                    max_tokens=16000,
                    system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": [bloco, {"type": "text", "text": texto_pedido}]}],
                    **extra,
                )
            except anthropic.BadRequestError as e:
                recusas.append(f"[{modo}] {e}")
                log.warning("A IA recusou o pedido no modo %s: %s", modo, e)
                continue
            u = resp.usage
            log.info("IA (%s): entrada=%s cache_lido=%s saída=%s", modo, u.input_tokens,
                     getattr(u, "cache_read_input_tokens", None), u.output_tokens)
            if resp.stop_reason == "refusal":
                return Extracao(recusado=True, motivo_classificacao="a IA recusou ler o documento")
            if resp.stop_reason == "max_tokens":
                raise RuntimeError("resposta da IA cortada (max_tokens)")
            texto = "".join(b.text for b in resp.content if b.type == "text")
            ext = interpretar_resposta(texto)
            ext.bruto["_modo"] = modo
            if recusas:
                ext.observacoes = ((ext.observacoes or "") + f" [lido no modo {modo}]").strip()
            return ext
        raise RuntimeError("o serviço da IA recusou todos os formatos de pedido: " + " || ".join(recusas))
