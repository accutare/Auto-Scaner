"""Mapa de pastas: lê (sem mexer) todas as pastas da pasta principal do escritório e gera
"Docflow/Mapa de pastas.xlsx" no Dropbox e dados/mapa.json no servidor (usado para achar pastas por nome).

Configuração (.env): MAPA_RAIZ, MAPA_PROFUNDIDADE (níveis a partir da raiz), MAPA_IGNORAR (pastas de
primeiro nível a deixar de fora, separadas por ";").
Uso no servidor:  docker compose run --rm web python -m app.mapa
"""
import io
import json
import logging
import os
import re
import sys
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

log = logging.getLogger("docflow.mapa")
RE_CODIGO = re.compile(r"^([A-Za-z]{1,4}\d{2,}[A-Za-z]{0,4}|\d{1,3})(?=$|[\s\-_.])")


def codigo_de(nome: str) -> str:
    m = RE_CODIGO.match(nome.strip())
    return m.group(1) if m else ""


def montar(dbx, raiz: str, profundidade: int, ignorar: list[str]) -> list[dict]:
    raiz = raiz.rstrip("/")
    ignorar_l = {i.strip().lower() for i in ignorar if i.strip()}
    linhas = []
    for primeiro in dbx.listar_pastas(raiz):
        if primeiro.lower() in ignorar_l:
            continue
        base = f"{raiz}/{primeiro}"
        caminhos = [base] + dbx.listar_pastas_recursivo(base)
        for c in caminhos:
            rel = c[len(raiz) + 1:] if c.lower().startswith(raiz.lower() + "/") else c
            partes = rel.split("/")
            if len(partes) > profundidade:
                continue
            nome = partes[-1]
            linhas.append({"nivel": len(partes), "caminho": rel, "pasta": nome,
                           "pai": "/".join(partes[:-1]), "codigo": codigo_de(nome)})
    linhas.sort(key=lambda l: l["caminho"].lower())
    return linhas


def planilha(linhas: list[dict], raiz: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Mapa"
    ws.append(["Nível", "Caminho (a partir da pasta principal)", "Pasta", "Código", "Dentro de"])
    for cel in ws[1]:
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill("solid", fgColor="305496")
    for l in linhas:
        ws.append([l["nivel"], l["caminho"], l["pasta"], l["codigo"], l["pai"]])
    for col, w in zip("ABCDE", (7, 110, 45, 12, 80)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    info = wb.create_sheet("Sobre")
    for t in (f"Pasta principal: {raiz}", f"Gerado em: {datetime.now():%d/%m/%Y %H:%M}",
              f"Pastas no mapa: {len(linhas)}", "Só leitura: este mapa não move nem cria nada."):
        info.append([t])
    info.column_dimensions["A"].width = 90
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    from . import config as cfgmod
    from .dropbox_client import DropboxClient

    cfg = cfgmod.carregar()
    dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token,
                        cfg.dropbox_team_space)
    raiz = os.environ.get("MAPA_RAIZ", "/!Accurate Complete Accounting Ltd - data files")
    prof = int(os.environ.get("MAPA_PROFUNDIDADE", "5"))
    ignorar = os.environ.get("MAPA_IGNORAR", "ZZBackups").split(";")
    log.info("Lendo as pastas de %s (até %d níveis, ignorando %s)...", raiz, prof, ignorar)
    linhas = montar(dbx, raiz, prof, ignorar)
    os.makedirs(cfg.dados_dir, exist_ok=True)
    with open(os.path.join(cfg.dados_dir, "mapa.json"), "w", encoding="utf-8") as f:
        json.dump({"raiz": raiz, "gerado_em": datetime.now().isoformat(), "pastas": linhas}, f, ensure_ascii=False)
    dbx.enviar("/Docflow/Mapa de pastas.xlsx", planilha(linhas, raiz))
    log.info("Mapa pronto: %d pastas. Enviado para Docflow/Mapa de pastas.xlsx", len(linhas))


if __name__ == "__main__":
    main()
