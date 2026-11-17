"""App da pessoa do scanner: ela escreve "5715 > AN1000 > AN1000BL > 11", confere o caminho e confirma.

Roda no servidor (atrás do Caddy, com HTTPS) e fala direto com o Dropbox. Login: usuário "scanner" e a senha
gerada no primeiro uso (guardada em /data/app_senha.txt e enviada para o Dropbox em "Docflow/Senha do app.txt").
Regras: só move depois de "Mover agora", nunca cria pasta, nunca exclui nada.
"""
import hashlib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from . import config as cfgmod
from .comandos import interpretar, resolver_pedido
from .dropbox_client import DropboxClient
from .registro import Registro

log = logging.getLogger("docflow.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

cfg = cfgmod.carregar()
os.makedirs(cfg.dados_dir, exist_ok=True)
dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token, cfg.dropbox_team_space)
registro = Registro(os.path.join(cfg.dados_dir, "controle.db"))
RAIZ = os.environ.get("PASTA_RAIZ_CODIGOS", cfg.pasta_clientes).rstrip("/")
USUARIO = "scanner"
EXTENSOES = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")


def _senha() -> str:
    """Senha do app: APP_SENHA no .env, ou gerada uma vez e guardada no servidor e no Dropbox."""
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
        log.exception("Não consegui enviar a senha para o Dropbox (ela está em %s)", arq)
    return senha


SENHA = _senha()
seguranca = HTTPBasic()


def autenticar(cred: HTTPBasicCredentials = Depends(seguranca)) -> None:
    ok_u = secrets.compare_digest(cred.username.encode(), USUARIO.encode())
    ok_s = secrets.compare_digest(cred.password.encode(), SENHA.encode())
    if not (ok_u and ok_s):
        time.sleep(1)  # freia tentativas de adivinhar a senha
        raise HTTPException(401, "Usuário ou senha incorretos", headers={"WWW-Authenticate": "Basic"})


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _scans():
    itens = [a for a in dbx.listar(cfg.pasta_entrada) if a.nome.lower().endswith(EXTENSOES)]
    return sorted(itens, key=lambda a: a.modificado, reverse=True)


class Texto(BaseModel):
    texto: str


class Confirmacao(BaseModel):
    texto: str
    arquivo: str


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(autenticar)])
def pagina():
    with open(os.path.join(os.path.dirname(__file__), "web.html"), encoding="utf-8") as f:
        # sem cache: depois de uma atualização, recarregar a página já mostra a versão nova
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


@app.get("/api/scans", dependencies=[Depends(autenticar)])
def scans():
    return [{"nome": a.nome, "caminho": a.caminho, "hora": datetime.fromtimestamp(a.modificado, timezone.utc).astimezone()
             .strftime("%d/%m %H:%M")} for a in _scans()]


@app.post("/api/resolver", dependencies=[Depends(autenticar)])
def resolver(p: Texto):
    pedido = interpretar(p.texto)
    if not pedido:
        return {"ok": False, "mensagem": "Escreva o final do arquivo e os códigos, por exemplo: "
                                          "5715 > AN1000 > AN1000BL > 11"}
    return resolver_pedido(pedido, _scans(), RAIZ, dbx.listar_pastas).__dict__


@app.post("/api/mover", dependencies=[Depends(autenticar)])
def mover(c: Confirmacao):
    # Refaz a conferência no servidor: só move exatamente o que foi mostrado na tela
    pedido = interpretar(c.texto)
    if not pedido:
        raise HTTPException(400, "Comando inválido")
    r = resolver_pedido(pedido, _scans(), RAIZ, dbx.listar_pastas)
    if not r.ok or r.arquivo["caminho"] != c.arquivo:
        return {"ok": False, "mensagem": "O arquivo ou as pastas mudaram desde a conferência. Envie o comando de novo."}
    if not r.destino.startswith(RAIZ + "/"):
        raise HTTPException(400, "Destino fora da pasta principal")
    final = dbx.mover(r.arquivo["caminho"], f"{r.destino}/{r.arquivo['nome']}")
    registro.inserir(
        status="OK", cliente=r.caminho[0] if r.caminho else None, servico=" / ".join(r.caminho[1:]) or None,
        motivo=f"comando do app: {c.texto.strip()}", arquivo_original=r.arquivo["nome"],
        caminho_original=r.arquivo["caminho"], caminho_final=final,
        hash_arquivo=hashlib.sha256(final.encode()).hexdigest(),
    )
    try:
        dbx.enviar(f"{cfg.pasta_controle}/Controle.xlsx", registro.exportar_excel())
    except Exception:  # noqa: BLE001
        log.exception("Não consegui atualizar o Controle.xlsx")
    log.info("Movido pelo app: %s -> %s", r.arquivo["caminho"], final)
    return {"ok": True, "mensagem": "Movido.", "final": final, "caminho": r.caminho}


# ---------- Escolher a pasta pelo mapa (navegando ou buscando pelo nome) ----------
RAIZ_MAPA = os.environ.get("MAPA_RAIZ", "/!Accurate Complete Accounting Ltd - data files").rstrip("/")
IGNORAR_MAPA = {i.strip().lower() for i in os.environ.get("MAPA_IGNORAR", "ZZBackups").split(";") if i.strip()}


def _rel_valido(rel: str) -> str:
    rel = (rel or "").strip().strip("/")
    partes = [p for p in rel.split("/") if p]
    if any(p in (".", "..") for p in partes):
        raise HTTPException(400, "Caminho inválido")
    if partes and partes[0].lower() in IGNORAR_MAPA:
        raise HTTPException(400, "Pasta fora do mapa")
    return "/".join(partes)


@app.get("/api/pastas", dependencies=[Depends(autenticar)])
def pastas(caminho: str = ""):
    rel = _rel_valido(caminho)
    nomes = dbx.listar_pastas(f"{RAIZ_MAPA}/{rel}" if rel else RAIZ_MAPA)
    if not rel:
        nomes = [n for n in nomes if n.lower() not in IGNORAR_MAPA]
    return {"caminho": rel, "pastas": nomes}


def _normalizar(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


@app.get("/api/buscar", dependencies=[Depends(autenticar)])
def buscar(q: str = ""):
    palavras = _normalizar(q).split()
    arq = os.path.join(cfg.dados_dir, "mapa.json")
    if not palavras:
        return {"resultados": []}
    if not os.path.exists(arq):
        return {"resultados": [], "aviso": "O mapa de pastas ainda está sendo gerado. Use a navegação por enquanto."}
    with open(arq, encoding="utf-8") as f:
        mapa = json.load(f)
    res = []
    for l in mapa.get("pastas", []):
        alvo = _normalizar(l["caminho"])
        if all(p in alvo for p in palavras):
            # pastas cujo próprio nome bate vêm primeiro, depois as mais curtas
            nome = _normalizar(l["pasta"])
            res.append((0 if any(p in nome for p in palavras) else 1, l["nivel"], l["caminho"]))
    res.sort()
    return {"resultados": [r[2] for r in res[:40]]}


class Destino(BaseModel):
    arquivo: str
    destino: str


@app.post("/api/mover_pasta", dependencies=[Depends(autenticar)])
def mover_pasta(d: Destino):
    rel = _rel_valido(d.destino)
    if not rel:
        raise HTTPException(400, "Escolha uma pasta")
    scans_atuais = {a.caminho: a for a in _scans()}
    arq = scans_atuais.get(d.arquivo)
    if not arq:
        return {"ok": False, "mensagem": "Esse scan não está mais na pasta de scans. Atualize a lista."}
    pasta = f"{RAIZ_MAPA}/{rel}"
    if not dbx.existe(pasta):
        return {"ok": False, "mensagem": "Essa pasta não existe mais. Escolha de novo."}
    final = dbx.mover(arq.caminho, f"{pasta}/{arq.nome}")
    partes = rel.split("/")
    registro.inserir(
        status="OK", cliente=partes[1] if len(partes) > 1 else partes[0], servico=" / ".join(partes[2:]) or None,
        motivo="escolhido no mapa do app", arquivo_original=arq.nome, caminho_original=arq.caminho,
        caminho_final=final, hash_arquivo=arq.content_hash or hashlib.sha256(final.encode()).hexdigest(),
    )
    try:
        dbx.enviar(f"{cfg.pasta_controle}/Controle.xlsx", registro.exportar_excel())
    except Exception:  # noqa: BLE001
        log.exception("Não consegui atualizar o Controle.xlsx")
    log.info("Movido pelo mapa: %s -> %s", arq.caminho, final)
    return {"ok": True, "final": final, "caminho": partes}


@app.get("/saude")
def saude():
    return {"ok": True}
