"""Cliente Dropbox: listar, baixar, mover e enviar arquivos."""
import logging
import time
from dataclasses import dataclass

import dropbox
from dropbox.exceptions import ApiError
from dropbox.files import FileMetadata, WriteMode

log = logging.getLogger("docflow.dropbox")


@dataclass
class ArquivoRemoto:
    caminho: str          # path_display no Dropbox
    nome: str
    tamanho: int
    modificado: float     # timestamp (UTC)
    content_hash: str


class DropboxClient:
    def __init__(self, app_key: str, app_secret: str, refresh_token: str):
        self.dbx = dropbox.Dropbox(
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
            app_secret=app_secret,
        )

    def garantir_pasta(self, caminho: str) -> None:
        try:
            self.dbx.files_create_folder_v2(caminho)
        except ApiError as e:
            # Já existe: ignora
            if not (e.error.is_path() and e.error.get_path().is_conflict()):
                raise

    def listar(self, pasta: str) -> list[ArquivoRemoto]:
        itens: list[ArquivoRemoto] = []
        try:
            res = self.dbx.files_list_folder(pasta)
        except ApiError as e:
            if e.error.is_path() and e.error.get_path().is_not_found():
                self.garantir_pasta(pasta)
                return []
            raise
        while True:
            for entry in res.entries:
                if isinstance(entry, FileMetadata):
                    itens.append(
                        ArquivoRemoto(
                            caminho=entry.path_display,
                            nome=entry.name,
                            tamanho=entry.size,
                            modificado=entry.server_modified.timestamp(),
                            content_hash=entry.content_hash or "",
                        )
                    )
            if not res.has_more:
                break
            res = self.dbx.files_list_folder_continue(res.cursor)
        return itens

    def estavel(self, arq: ArquivoRemoto, segundos: int) -> bool:
        """Arquivo só é processado se não foi modificado nos últimos N segundos
        (evita ler arquivo ainda sincronizando/escaneando)."""
        return (time.time() - arq.modificado) >= segundos and arq.tamanho > 0

    def baixar(self, caminho: str) -> bytes:
        _, resp = self.dbx.files_download(caminho)
        return resp.content

    def mover(self, origem: str, destino: str) -> str:
        """Move criando pastas; se já existir arquivo igual, acrescenta sufixo."""
        pasta_destino = destino.rsplit("/", 1)[0]
        self._garantir_pasta_recursiva(pasta_destino)
        final = destino
        tentativa = 1
        while True:
            try:
                meta = self.dbx.files_move_v2(origem, final, autorename=False)
                return meta.metadata.path_display
            except ApiError as e:
                if e.error.is_to() and e.error.get_to().is_conflict():
                    tentativa += 1
                    base, _, ext = destino.rpartition(".")
                    final = f"{base}_{tentativa}.{ext}" if base else f"{destino}_{tentativa}"
                    continue
                raise

    def enviar(self, caminho: str, conteudo: bytes) -> None:
        self._garantir_pasta_recursiva(caminho.rsplit("/", 1)[0])
        self.dbx.files_upload(conteudo, caminho, mode=WriteMode.overwrite)

    def _garantir_pasta_recursiva(self, caminho: str) -> None:
        partes = [p for p in caminho.split("/") if p]
        atual = ""
        for parte in partes:
            atual += "/" + parte
            self.garantir_pasta(atual)
