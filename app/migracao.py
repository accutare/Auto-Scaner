"""Plano de migração: da estrutura por serviço para a estrutura por cliente.

Hoje:      Client Data / UK - VAT MTD Ready / <Cliente> / ...
Proposta:  Client Data / Accurate UK / <Cliente> / VAT / ...

Este módulo SÓ LÊ o Dropbox e gera a planilha "Plano de migração.xlsx" (pasta Controle), para a
equipe conferir antes de qualquer pasta ser movida. Rode no servidor:

    docker compose run --rm docflow python -m app.migracao
"""
import difflib
import io
import os
import re
from collections import Counter
from dataclasses import dataclass

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .catalogo import Catalogo, normalizar

PAISES = {
    "uk": "Accurate UK",
    "ireland": "Accurate Irlanda",
    "irlanda": "Accurate Irlanda",
    "brazil": "Accurate Brasil",
    "brasil": "Accurate Brasil",
    "portugal": "Accurate Portugal",
}

# Serviço de hoje (parte depois de "UK - ") -> nome da subpasta dentro do cliente.
# A primeira regra cujo termo aparece no nome do serviço vale. Ajuste aqui se a equipe aprovar outros nomes.
SUBPASTAS = [
    ("vat", "VAT"),
    ("self-assessment", "Self-Assessment"),
    ("self assessment", "Self-Assessment"),
    ("payroll", "Payroll"),
    ("ltd compan", "Ltd Company - Year End"),
    ("insolvency", "Insolvency"),
    ("identity verif", "Identity Verification"),
    ("cis suffered", "CIS Suffered"),
    ("cis deducted", "CIS Deducted"),
    ("bookkeeping", "Bookkeeping"),
    ("charities", "Bookkeeping"),
    ("annual statements", "Annual Statements"),
    ("compliance", "Compliance"),
    ("other services", "Other Services"),
    ("vip", "VIP"),
    ("clients", "General"),
]

A_CONFIRMAR = {"VIP", "General", "Annual Statements", "Compliance"}
PROIBIDOS = re.compile(r'[<>:"/\\|?*]')


def pais_de(servico: str) -> str:
    prefixo = servico.split(" - ", 1)[0].strip()
    return PAISES.get(prefixo.lower(), f"Accurate {prefixo}")


def subpasta_de(servico: str) -> str:
    nome = servico.split(" - ", 1)[-1].lower()
    for termo, sub in SUBPASTAS:
        if termo in nome:
            return sub
    return servico.split(" - ", 1)[-1].strip(" !")


def nome_seguro(nome: str) -> str:
    n = PROIBIDOS.sub("", nome)
    return re.sub(r"\s+", " ", n).strip(" .")


@dataclass
class LinhaPlano:
    cliente_id: str
    cliente: str
    contatos: str
    pais: str
    servico_atual: str
    pasta_atual: str
    pasta_nova: str
    observacao: str


def montar_plano(cat: Catalogo) -> list[LinhaPlano]:
    linhas: list[LinhaPlano] = []
    destinos: Counter = Counter()
    for c in cat.clientes.values():
        nome_cli = nome_seguro(c.nome)
        for servico, pasta in sorted(c.todas_pastas):
            pais = pais_de(servico)
            sub = subpasta_de(servico)
            destino = f"{cat.raiz}/{pais}/{nome_cli}/{sub}"
            obs = []
            destinos[destino] += 1
            if destinos[destino] > 1:
                destino = f"{destino} ({destinos[destino]})"
                obs.append("o cliente já tem outra pasta indo para este mesmo lugar: juntar à mão depois")
            if sub in A_CONFIRMAR:
                obs.append(f"confirmar se '{sub}' deve ser uma subpasta do cliente")
            variantes = sorted(v for v in c.variantes if v != c.nome)
            if variantes and not any(o.startswith("nomes") for o in obs):
                obs.append("nomes diferentes juntados: " + "; ".join(variantes))
            linhas.append(LinhaPlano(
                c.id, nome_cli, ", ".join(sorted(c.contatos)), pais, servico,
                f"{cat.raiz}/{servico}/{pasta}", destino, " | ".join(obs),
            ))
    return linhas


def possiveis_duplicados(cat: Catalogo, corte: float = 0.88) -> list[tuple[str, str, str, str, float]]:
    """Pares de clientes com nomes parecidos que NÃO foram juntados automaticamente."""
    itens = [(c.id, c.nome, normalizar(c.nome)) for c in cat.clientes.values()]
    blocos: dict[str, list] = {}
    for it in itens:
        blocos.setdefault(it[2][:2], []).append(it)
    pares = []
    for grupo in blocos.values():
        for i in range(len(grupo)):
            for j in range(i + 1, len(grupo)):
                a, b = grupo[i], grupo[j]
                sm = difflib.SequenceMatcher(None, a[2], b[2])
                if sm.real_quick_ratio() < corte or sm.quick_ratio() < corte:
                    continue
                r = sm.ratio()
                if r >= corte:
                    pares.append((a[0], a[1], b[0], b[1], round(r, 2)))
    return sorted(pares, key=lambda p: -p[4])


def _cabecalho(ws, titulos, larguras):
    ws.append(titulos)
    for cel in ws[1]:
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill("solid", fgColor="305496")
    for i, w in enumerate(larguras, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def gerar_planilha(cat: Catalogo, linhas: list[LinhaPlano], pares) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Leia-me"
    texto = [
        "PLANO DE MIGRAÇÃO - nada foi movido ainda",
        "",
        "Aba 'Plano': cada linha é uma pasta de hoje e para onde ela iria na estrutura nova.",
        "  Coluna 'Aprovar?': deixe em branco se estiver certo. Escreva NÃO para não mover a pasta.",
        "  Para mudar o destino, corrija a coluna 'Pasta nova'.",
        "Aba 'Possíveis duplicados': clientes com nomes parecidos que o programa NÃO juntou.",
        "  Escreva SIM na coluna 'Juntar?' quando forem o mesmo cliente.",
        "Aba 'Serviços': como cada pasta de serviço vira uma subpasta dentro do cliente.",
        "",
        f"Pastas de clientes encontradas: {len(linhas)}",
        f"Clientes (depois de juntar nomes iguais): {len(cat.clientes)}",
        f"Possíveis duplicados para conferir: {len(pares)}",
    ]
    for t in texto:
        ws.append([t])
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 110

    ws = wb.create_sheet("Plano")
    _cabecalho(ws, ["Código", "Cliente (nome proposto)", "Contato(s)", "País", "Serviço atual",
                    "Pasta atual", "Pasta nova", "Observação", "Aprovar?"],
               [8, 42, 30, 18, 34, 70, 70, 60, 10])
    amarelo = PatternFill("solid", fgColor="FFF2CC")
    for l in linhas:
        ws.append([l.cliente_id, l.cliente, l.contatos, l.pais, l.servico_atual,
                   l.pasta_atual, l.pasta_nova, l.observacao, ""])
        if l.observacao:
            for cel in ws[ws.max_row]:
                cel.fill = amarelo
    ws.auto_filter.ref = ws.dimensions

    ws = wb.create_sheet("Possíveis duplicados")
    _cabecalho(ws, ["Código A", "Cliente A", "Código B", "Cliente B", "Semelhança", "Juntar? (SIM/NÃO)"],
               [10, 50, 10, 50, 12, 18])
    for p in pares:
        ws.append(list(p) + [""])
    ws.auto_filter.ref = ws.dimensions

    ws = wb.create_sheet("Serviços")
    _cabecalho(ws, ["Pasta de serviço hoje", "País novo", "Subpasta dentro do cliente", "Pastas"],
               [45, 22, 30, 10])
    cont = Counter(l.servico_atual for l in linhas)
    for s in cat.servicos:
        ws.append([s, pais_de(s), subpasta_de(s), cont.get(s, 0)])
    for row in wb["Leia-me"].iter_rows():
        for cel in row:
            cel.alignment = Alignment(wrap_text=False)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


if __name__ == "__main__":
    from . import config as cfgmod
    from .catalogo import carregar_extras, montar_catalogo
    from .dropbox_client import DropboxClient

    cfg = cfgmod.carregar()
    dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token,
                        cfg.dropbox_team_space)
    print("Lendo as pastas do Dropbox (só leitura)...")
    cat = montar_catalogo(cfg.pasta_client_data, dbx.listar_pastas, carregar_extras(cfg.clientes_extra_csv))
    linhas = montar_plano(cat)
    pares = possiveis_duplicados(cat)
    dados = gerar_planilha(cat, linhas, pares)
    os.makedirs(cfg.dados_dir, exist_ok=True)
    with open(os.path.join(cfg.dados_dir, "Plano de migração.xlsx"), "wb") as f:
        f.write(dados)
    destino = f"{cfg.pasta_controle}/Plano de migração.xlsx"
    dbx.enviar(destino, dados)
    print(f"Pastas de clientes: {len(linhas)}")
    print(f"Clientes (depois de juntar nomes iguais): {len(cat.clientes)}")
    print(f"Possíveis duplicados para conferir: {len(pares)}")
    print(f"Planilha salva no Dropbox em: {destino}")
    print("Nenhuma pasta foi movida.")
