"""Envia o relatório de status do servidor (dados/status.txt) para o Dropbox: Docflow/Status do servidor.txt.
Assim dá para ver o que o servidor está fazendo sem abrir o Terminal."""
import sys

from . import config as cfgmod
from .dropbox_client import DropboxClient

cfg = cfgmod.carregar()
dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token, cfg.dropbox_team_space)
with open(sys.argv[1], "rb") as f:
    dbx.enviar("/Docflow/Status do servidor.txt", f.read())
