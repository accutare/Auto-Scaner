"""Scanner app (English): the operator sees each waiting scan with a picture of its first page, ticks one
or many, and sends them to a folder by choosing it on the folder map or by typing a path command
("move 5715 5716 to AN1000 / AN1000BL / 11").

Runs on the server (behind Caddy, with HTTPS) and talks straight to Dropbox. Login: user "scanner" and the
password generated on first use (kept in /data/app_senha.txt and sent to Dropbox "Docflow/Senha do app.txt").
Rules: it only moves after the operator confirms, never creates folders, never deletes anything, and only
moves into folders under the allowed roots.
"""
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from . import config as cfgmod
from .comandos import escolher_scans, interpretar, normalizar, resolver
from .dropbox_client import DropboxClient
from .registro import Registro

log = logging.getLogger("docflow.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

cfg = cfgmod.carregar()
os.makedirs(cfg.dados_dir, exist_ok=True)
dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token, cfg.dropbox_team_space)
registro = Registro(os.path.join(cfg.dados_dir, "controle.db"))
USUARIO = "scanner"
EXTENSOES = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")
TIPOS = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".tif": "image/tiff", ".tiff": "image/tiff"}

# Folders the app may move into (searched in this order when a command matches in both)
RAIZ_CODIGOS = os.environ.get("PASTA_RAIZ_CODIGOS", cfg.pasta_clientes).rstrip("/")
RAIZ_MAPA = os.environ.get("MAPA_RAIZ", "/!Accurate Complete Accounting Ltd - data files").rstrip("/")
IGNORAR_MAPA = {i.strip().lower() for i in os.environ.get("MAPA_IGNORAR", "").split(";") if i.strip()}
RAIZES = [r for r in dict.fromkeys([RAIZ_CODIGOS, RAIZ_MAPA]) if r]


def _senha() -> str:
    """App password: APP_SENHA in .env, or generated once and kept on the server and in Dropbox."""
    if os.environ.get("APP_SENHA"):
        return os.environ["APP_SENHA"]
    arq = os.path.join(cfg.dados_dir, "app_senha.txt")
    if os.path.exists(arq):
        return open(arq, encoding="utf-8").read().strip()
    senha = secrets.token_urlsafe(12)
    with open(arq, "w", encoding="utf-8") as f:
        f.write(senha)
    os.chmod(arq, 0o600)
    try:
        texto = (f"Docflow - acesso ao app do scanner\n\nUsuario: {USUARIO}\nSenha: {senha}\n\n"
                 "Guarde num lugar seguro. Pode mover ou apagar este arquivo depois.\n")
        dbx.enviar("/Docflow/Senha do app.txt", texto.encode("utf-8"))
    except Exception:  # noqa: BLE001
        log.exception("Could not send the password to Dropbox (it is in %s)", arq)
    return senha


SENHA = _senha()
seguranca = HTTPBasic()


def autenticar(cred: HTTPBasicCredentials = Depends(seguranca)) -> None:
    ok_u = secrets.compare_digest(cred.username.encode(), USUARIO.encode())
    ok_s = secrets.compare_digest(cred.password.encode(), SENHA.encode())
    if not (ok_u and ok_s):
        time.sleep(1)  # slows down password guessing
        raise HTTPException(401, "Wrong user or password", headers={"WWW-Authenticate": "Basic"})


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _scans():
    itens = [a for a in dbx.listar(cfg.pasta_entrada) if a.nome.lower().endswith(EXTENSOES)]
    return sorted(itens, key=lambda a: a.modificado, reverse=True)


# ---------------------------------------------------------------- allowed folders
def _raiz_de(caminho: str) -> str | None:
    c = (caminho or "").rstrip("/")
    partes = [p for p in c.split("/") if p]
    if any(p in (".", "..") for p in partes):
        return None
    for r in RAIZES:
        if c.lower() == r.lower() or c.lower().startswith(r.lower() + "/"):
            resto = c[len(r):].strip("/").split("/")
            if r == RAIZ_MAPA and resto and resto[0] and resto[0].lower() in IGNORAR_MAPA:
                return None
            return r
    return None


def _rotulo(caminho: str) -> list[str]:
    """Path as the operator reads it: the root's own name, then the folders below it."""
    r = _raiz_de(caminho) or ""
    resto = [p for p in caminho[len(r):].split("/") if p]
    return [r.split("/")[-1]] + resto


_indice = {"t": 0.0, "mapa_mtime": None, "itens": []}
_trava = threading.Lock()


def indice() -> list[tuple[str, int, list[str]]]:
    """Every folder the app may move into: the daily folder map + a live listing of the codes root."""
    arq = os.path.join(cfg.dados_dir, "mapa.json")
    mtime = os.path.getmtime(arq) if os.path.exists(arq) else None
    with _trava:
        if _indice["itens"] and time.time() - _indice["t"] < 600 and _indice["mapa_mtime"] == mtime:
            return _indice["itens"]
        caminhos: list[str] = []
        for r in RAIZES:
            if r == RAIZ_MAPA and mtime is not None:
                with open(arq, encoding="utf-8") as f:
                    mapa = json.load(f)
                base = (mapa.get("raiz") or RAIZ_MAPA).rstrip("/")
                caminhos += [f"{base}/{l['caminho']}" for l in mapa.get("pastas", [])]
            elif r != RAIZ_MAPA:
                try:
                    caminhos += dbx.listar_pastas_recursivo(r)
                except Exception:  # noqa: BLE001
                    log.exception("Could not list %s", r)
        itens, vistos = [], set()
        for c in caminhos:
            raiz = _raiz_de(c)
            if not raiz or c.lower() in vistos:
                continue
            vistos.add(c.lower())
            itens.append((c, RAIZES.index(raiz), [p for p in c[len(raiz):].split("/") if p]))
        _indice.update(t=time.time(), mapa_mtime=mtime, itens=itens)
        return itens


# ---------------------------------------------------------------- pages and data
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(autenticar)])
def pagina():
    with open(os.path.join(os.path.dirname(__file__), "web.html"), encoding="utf-8") as f:
        # never cached: after an update, a reload shows the new version
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


def _scan_json(a):
    return {"nome": a.nome, "caminho": a.caminho, "tamanho": a.tamanho,
            "hora": datetime.fromtimestamp(a.modificado, timezone.utc).astimezone().strftime("%d/%m %H:%M")}


@app.get("/api/scans", dependencies=[Depends(autenticar)])
def scans():
    return [_scan_json(a) for a in _scans()]


@app.get("/api/arquivo", dependencies=[Depends(autenticar)])
def arquivo(caminho: str):
    """The scan itself, to show its first page and open it in full. Only files waiting in Scans."""
    arq = {a.caminho: a for a in _scans()}.get(caminho)
    if not arq:
        raise HTTPException(404, "This scan is no longer in the Scans folder")
    ext = os.path.splitext(arq.nome)[1].lower()
    return Response(dbx.baixar(arq.caminho), media_type=TIPOS.get(ext, "application/octet-stream"),
                    headers={"Cache-Control": "private, max-age=600",
                             "Content-Disposition": f'inline; filename="{arq.nome.encode("ascii", "ignore").decode()}"'})


@app.get("/api/pastas", dependencies=[Depends(autenticar)])
def pastas(caminho: str = ""):
    if not caminho.strip("/"):
        return {"caminho": "", "trilha": [], "pastas": [{"nome": r.split("/")[-1], "caminho": r} for r in RAIZES]}
    raiz = _raiz_de(caminho)
    if not raiz:
        raise HTTPException(400, "This folder is outside the allowed folders")
    c = caminho.rstrip("/")
    nomes = dbx.listar_pastas(c)
    if c.lower() == RAIZ_MAPA.lower():
        nomes = [n for n in nomes if n.lower() not in IGNORAR_MAPA]
    return {"caminho": c, "trilha": _rotulo(c), "pastas": [{"nome": n, "caminho": f"{c}/{n}"} for n in nomes]}


@app.get("/api/buscar", dependencies=[Depends(autenticar)])
def buscar(q: str = ""):
    palavras = normalizar(q).split()
    if not palavras:
        return {"resultados": []}
    res = []
    for caminho, prio, partes in indice():
        alvo = normalizar(" ".join(partes)).split()
        if all(any(p.startswith(w) for p in alvo) for w in palavras):
            nome = normalizar(partes[-1]).split()
            proprio = any(any(p.startswith(w) for p in nome) for w in palavras)
            res.append((0 if proprio else 1, len(partes), prio, caminho))
    res.sort()
    aviso = "" if os.path.exists(os.path.join(cfg.dados_dir, "mapa.json")) else \
        "The folder map is still being built, so some folders may be missing. You can browse instead."
    return {"resultados": [{"caminho": r[3], "trilha": _rotulo(r[3])} for r in res[:40]], "aviso": aviso}


class Comando(BaseModel):
    texto: str
    selecionados: list[str] = []


@app.post("/api/comando", dependencies=[Depends(autenticar)])
def comando(c: Comando):
    """Works out which scans and which folder a typed command means. Moves nothing."""
    cmd = interpretar(c.texto)
    if not cmd:
        return {"ok": False, "mensagem": "Tell me the scans and the folder, for example: "
                                         "move 5715 to AN1000 / AN1000BL / 11"}
    esc = escolher_scans(cmd, _scans(), c.selecionados)
    if not esc.ok:
        return {"ok": False, "mensagem": esc.mensagem, "numero": esc.numero,
                "opcoes_arquivo": [_scan_json(a) for a in esc.opcoes]}
    d = resolver(cmd, indice(), RAIZES, dbx.listar_pastas)
    base = {"arquivos": [_scan_json(a) for a in esc.arquivos]}
    if not d.ok:
        return {**base, "ok": False, "mensagem": d.mensagem,
                "opcoes_pasta": [{"caminho": o, "trilha": _rotulo(o)} for o in d.opcoes]}
    return {**base, "ok": True, "destino": d.caminho, "trilha": _rotulo(d.caminho)}


class Envio(BaseModel):
    arquivos: list[str]
    destino: str
    origem: str = ""          # how the folder was chosen (command text or "folder map"), for the log


@app.post("/api/mover", dependencies=[Depends(autenticar)])
def mover(e: Envio):
    """Moves one or many scans into one existing folder. No limit on how many."""
    if not e.arquivos:
        return {"ok": False, "mensagem": "Tick at least one scan."}
    destino = e.destino.rstrip("/")
    raiz = _raiz_de(destino)
    if not raiz or destino.lower() == raiz.lower():
        raise HTTPException(400, "Choose a folder inside the client folders")
    if not dbx.existe(destino):
        return {"ok": False, "mensagem": "That folder no longer exists. Choose it again."}
    atuais = {a.caminho: a for a in _scans()}
    trilha = _rotulo(destino)
    movidos, falhas = [], []
    for caminho in dict.fromkeys(e.arquivos):
        arq = atuais.get(caminho)
        nome = caminho.rsplit("/", 1)[-1]
        if not arq:
            falhas.append({"nome": nome, "motivo": "no longer in the Scans folder"})
            continue
        try:
            final = dbx.mover(arq.caminho, f"{destino}/{arq.nome}")
        except Exception:  # noqa: BLE001
            log.exception("Move failed: %s", arq.caminho)
            falhas.append({"nome": nome, "motivo": "Dropbox did not accept the move; it is still in Scans"})
            continue
        registro.inserir(
            status="OK", cliente=trilha[1] if len(trilha) > 1 else trilha[0],
            servico=" / ".join(trilha[2:]) or None, motivo=f"app: {(e.origem or 'folder map')[:200]}",
            arquivo_original=arq.nome, caminho_original=arq.caminho, caminho_final=final,
            hash_arquivo=arq.content_hash or None,
        )
        movidos.append({"nome": arq.nome, "final": final})
        log.info("Moved by the app: %s -> %s", arq.caminho, final)
    if movidos:
        try:
            dbx.enviar(f"{cfg.pasta_controle}/Controle.xlsx", registro.exportar_excel())
        except Exception:  # noqa: BLE001
            log.exception("Could not update Controle.xlsx")
    return {"ok": bool(movidos), "movidos": movidos, "falhas": falhas, "trilha": trilha}


@app.get("/saude")
def saude():
    return {"ok": True}
