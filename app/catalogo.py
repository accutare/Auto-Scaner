"""Catálogo de clientes e serviços, descoberto lendo a pasta "Client Data" do Dropbox.

A estrutura atual do escritório é: Client Data / <Região - Serviço> / <Pasta do cliente> / ...
  ex.: "UK - VAT MTD Ready/Alves Marketing Business and Network Ltd TA FGV"
       "UK - Ltd Companies (Individual clients)/Alves Marketing Business and Network Ltd - Rafael Alves Pinto"

O mesmo cliente aparece em várias pastas de serviço, com nomes um pouco diferentes. Aqui juntamos
essas pastas num único cliente (pelo nome da empresa normalizado), para a IA escolher o cliente
e o serviço certos. Novas pastas de serviço (ex.: "Portugal - VAT") entram sozinhas no catálogo.

Rode `python -m app.catalogo` para ver como o programa enxergou os clientes.
"""
import csv
import os
import re
import unicodedata
from dataclasses import dataclass, field

# Pastas que começam com "!" são modelos/avisos internos (ex.: "!!Bridging Information and Templates")
PREFIXOS_IGNORADOS = ("!", ".", "~")


def normalizar(texto: str) -> str:
    """Chave de comparação: minúsculas, sem acento/pontuação, 'limited' -> 'ltd', '&' -> 'and'."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    t = t.replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\blimited\b", "ltd", t)
    t = re.sub(r"\bt a\b", "ta", t)  # "T/A" ou "T A" -> "ta"
    return re.sub(r"\s+", " ", t).strip()


ABREVIACOES = {
    "road": "rd", "street": "st", "avenue": "ave", "company": "co", "services": "svcs",
    "service": "svc", "management": "mgmt", "international": "intl", "the": "",
}


def chave_cliente(nome: str) -> str:
    """Chave para juntar as pastas do mesmo cliente: ignora espaços, pontuação e abreviações
    comuns ("2 GET Ltd" = "2Get Ltd", "Burnaby Rd" = "Burnaby Road", "Limited" = "Ltd")."""
    palavras = [ABREVIACOES.get(p, p) for p in normalizar(nome).split()]
    return "".join(palavras)


@dataclass
class NomePasta:
    empresa: str           # parte principal (nome da empresa ou da pessoa)
    contato: str | None    # pessoa após " - " ou entre parênteses
    nome_comercial: str | None  # após "TA" (trading as)


def interpretar_nome_pasta(nome: str) -> NomePasta:
    contato = None
    nome_comercial = None
    base = nome.strip()
    m = re.search(r"\(([^)]*)\)\s*$", base)
    if m:
        contato = m.group(1).strip() or None
        base = base[: m.start()].strip()
    if " - " in base:
        base, resto = base.split(" - ", 1)
        contato = contato or resto.strip() or None
    m = re.search(r"\s+(?:T/A|TA|T A)\s+(.+)$", base, flags=re.IGNORECASE)
    if m:
        nome_comercial = m.group(1).strip()
        base = base[: m.start()].strip()
    return NomePasta(base.strip(), contato, nome_comercial)


@dataclass
class Cliente:
    id: str
    nome: str
    chave: str
    contatos: set[str] = field(default_factory=set)
    nomes_comerciais: set[str] = field(default_factory=set)
    # serviço (nome da pasta de serviço) -> caminho completo da pasta do cliente nesse serviço
    pastas: dict[str, str] = field(default_factory=dict)
    identificadores: dict[str, str] = field(default_factory=dict)
    apelidos: set[str] = field(default_factory=set)
    # todas as pastas do cliente, inclusive repetidas no mesmo serviço: (serviço, nome da pasta)
    todas_pastas: list[tuple[str, str]] = field(default_factory=list)
    variantes: dict[str, int] = field(default_factory=dict)  # nome escrito -> quantas pastas usam


@dataclass
class Catalogo:
    raiz: str
    servicos: list[str]
    clientes: dict[str, Cliente]

    def por_id(self, cid: str | None) -> Cliente | None:
        return self.clientes.get((cid or "").strip().upper())

    def por_identificador(self, tipo: str, valor: str | None) -> Cliente | None:
        v = _so_alfanum(valor)
        if not v:
            return None
        for c in self.clientes.values():
            if _so_alfanum(c.identificadores.get(tipo)) == v:
                return c
        return None

    def texto_para_ia(self) -> str:
        """Lista compacta e estável (mesma ordem sempre: ajuda o cache do prompt)."""
        linhas = []
        for c in self.clientes.values():
            partes = [c.id, c.nome]
            if c.nomes_comerciais:
                partes.append("trading as: " + ", ".join(sorted(c.nomes_comerciais)))
            if c.contatos:
                partes.append("contato: " + ", ".join(sorted(c.contatos)))
            if c.apelidos:
                partes.append("também conhecido como: " + ", ".join(sorted(c.apelidos)))
            for k in sorted(c.identificadores):
                partes.append(f"{k}: {c.identificadores[k]}")
            partes.append("serviços: " + "; ".join(sorted(c.pastas)))
            linhas.append(" | ".join(partes))
        return "\n".join(linhas)


def _so_alfanum(v) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()


def montar_catalogo(raiz: str, listar_pastas, extras: list[dict] | None = None) -> Catalogo:
    """listar_pastas(caminho) -> lista de nomes de subpastas (só pastas)."""
    servicos = sorted(
        n for n in listar_pastas(raiz) if not n.startswith(PREFIXOS_IGNORADOS)
    )
    grupos: dict[str, Cliente] = {}
    for servico in servicos:
        for pasta in listar_pastas(f"{raiz}/{servico}"):
            if pasta.startswith(PREFIXOS_IGNORADOS):
                continue
            info = interpretar_nome_pasta(pasta)
            chave = chave_cliente(info.empresa)
            if not chave:
                continue
            c = grupos.get(chave)
            if c is None:
                c = grupos[chave] = Cliente(id="", nome=info.empresa, chave=chave)
            if info.contato:
                c.contatos.add(info.contato)
            if info.nome_comercial:
                c.nomes_comerciais.add(info.nome_comercial)
            # Se o mesmo cliente tiver 2 pastas no mesmo serviço, fica a primeira (ordem alfabética)
            c.pastas.setdefault(servico, f"{raiz}/{servico}/{pasta}")
            c.todas_pastas.append((servico, pasta))
            peso = 2 if "ltd compan" in servico.lower() else 1  # o nome da pasta de Ltd costuma ser o oficial
            c.variantes[info.empresa] = c.variantes.get(info.empresa, 0) + peso

    for c in grupos.values():
        # nome exibido: o mais usado (Ltd Companies vale mais); empate -> o mais longo
        c.nome = max(c.variantes, key=lambda n: (c.variantes[n], len(n), n))

    ordenados = sorted(grupos.values(), key=lambda c: c.chave)
    clientes: dict[str, Cliente] = {}
    for i, c in enumerate(ordenados, 1):
        c.id = f"C{i:04d}"
        clientes[c.id] = c

    for linha in extras or []:
        alvo = _achar_por_nome(clientes, linha.get("pasta") or "")
        if not alvo:
            continue
        for k in ("company_number", "vat_number", "utr", "paye_ref", "year_end"):
            v = (linha.get(k) or "").strip()
            if v:
                alvo.identificadores[k] = v
        for a in (linha.get("apelidos") or "").split("|"):
            if a.strip():
                alvo.apelidos.add(a.strip())
    return Catalogo(raiz, servicos, clientes)


def _achar_por_nome(clientes: dict[str, Cliente], nome: str) -> Cliente | None:
    chave = chave_cliente(interpretar_nome_pasta(nome).empresa)
    for c in clientes.values():
        if c.chave == chave:
            return c
    return None


def carregar_extras(caminho_csv: str) -> list[dict]:
    """clientes_extra.csv (opcional): pasta;company_number;vat_number;utr;paye_ref;year_end;apelidos
    year_end = dia/mês do fim do ano fiscal da empresa (ex.: 31/12). Sem ele, vale o padrão do servicos.json.
    'pasta' é o nome de qualquer pasta do cliente no Dropbox. Ajuda a IA a reconhecer o cliente
    por número (Companies House, VAT, UTR, PAYE) quando o nome no documento é diferente."""
    if not caminho_csv or not os.path.exists(caminho_csv):
        return []
    with open(caminho_csv, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(2048)
        f.seek(0)
        delim = ";" if amostra.count(";") >= amostra.count(",") else ","
        return [dict(l) for l in csv.DictReader(f, delimiter=delim)]


if __name__ == "__main__":
    import sys

    from . import config as cfgmod
    from .dropbox_client import DropboxClient

    cfg = cfgmod.carregar()
    dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token,
                        cfg.dropbox_team_space)
    cat = montar_catalogo(cfg.pasta_client_data, dbx.listar_pastas, carregar_extras(cfg.clientes_extra_csv))
    print(f"Pasta: {cat.raiz}")
    print(f"Serviços encontrados ({len(cat.servicos)}):")
    for s in cat.servicos:
        print("  -", s)
    print(f"\nClientes ({len(cat.clientes)}):")
    sys.stdout.write(cat.texto_para_ia() + "\n")
