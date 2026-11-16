"""App da pessoa do scanner: ela escreve "5715 > AN1000 > AN1000BL > 11", confere o caminho e confirma.

Roda no servidor (atrás do Caddy, com HTTPS) e fala direto com o Dropbox. Login: usuário "scanner" e a senha
gerada no primeiro uso (guardada em /data/app_senha.txt e enviada para o Dropbox em "Docflow/Senha do app.txt").
Regras: só move depois de "Mover agora", nunca cria pasta, nunca exclui nada.
"""
import hashlib
import logging
import os
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
        return f.read()


@app.get("/api/scans", dependencies=[Depends(autenticar)])
def scans():
    return [{"nome": a.nome, "hora": datetime.fromtimestamp(a.modificado, timezone.utc).astimezone()
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


@app.get("/saude")
def saude():
    return {"ok": True}
