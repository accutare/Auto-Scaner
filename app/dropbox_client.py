"""Cliente Dropbox: listar, baixar, mover e enviar arquivos."""
import logging
import time
from dataclasses import dataclass
from datetime import timezone

import dropbox
from dropbox.exceptions import ApiError
from dropbox.files import FileMetadata, FolderMetadata, WriteMode

log = logging.getLogger("docflow.dropbox")


def _utc(dt) -> float:
    """O Dropbox devolve a data em UTC sem fuso: marca como UTC antes de converter (evita erro de 1h no verão)."""
    return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()


@dataclass
class ArquivoRemoto:
    caminho: str          # path_display no Dropbox
    nome: str
    tamanho: int
    modificado: float     # timestamp (UTC)
    content_hash: str


class DropboxClient:
    def __init__(self, app_key: str, app_secret: str, refresh_token: str, team_space: str = "auto"):
        self.dbx = dropbox.Dropbox(
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
            app_secret=app_secret,
        )
        if team_space != "off":
            self._usar_espaco_da_equipe()

    def _usar_espaco_da_equipe(self) -> None:
        """Em contas Dropbox Business com "team space", as pastas da equipe (ex.: "!Accurate Complete
        Accounting Ltd - data files") ficam fora da raiz pessoal do usuário. Aqui passamos a usar a raiz
        da equipe, para os caminhos ficarem iguais aos que a equipe vê no Dropbox."""
        from dropbox.common import PathRoot

        conta = self.dbx.users_get_current_account()
        raiz = conta.root_info
        if raiz.root_namespace_id != raiz.home_namespace_id:
            self.dbx = self.dbx.with_path_root(PathRoot.root(raiz.root_namespace_id))
            log.info("Usando o espaço da equipe do Dropbox (namespace %s)", raiz.root_namespace_id)

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
                            modificado=_utc(entry.server_modified),
                            content_hash=entry.content_hash or "",
                        )
                    )
            if not res.has_more:
                break
            res = self.dbx.files_list_folder_continue(res.cursor)
        return itens

    def listar_pastas(self, pasta: str) -> list[str]:
        """Nomes das subpastas diretas (não recursivo). Pasta inexistente -> lista vazia."""
        nomes: list[str] = []
        try:
            res = self.dbx.files_list_folder(pasta)
        except ApiError as e:
            if e.error.is_path() and e.error.get_path().is_not_found():
                return []
            raise
        while True:
            nomes += [e.name for e in res.entries if isinstance(e, FolderMetadata)]
            if not res.has_more:
                break
            res = self.dbx.files_list_folder_continue(res.cursor)
        return sorted(nomes)

    def listar_recursivo(self, pasta: str) -> list[ArquivoRemoto]:
        """Todos os arquivos dentro da pasta e subpastas. Pasta inexistente -> lista vazia."""
        itens: list[ArquivoRemoto] = []
        try:
            res = self.dbx.files_list_folder(pasta, recursive=True)
        except ApiError as e:
            if e.error.is_path() and e.error.get_path().is_not_found():
                return []
            raise
        while True:
            for entry in res.entries:
                if isinstance(entry, FileMetadata):
                    itens.append(ArquivoRemoto(entry.path_display, entry.name, entry.size,
                                               _utc(entry.server_modified), entry.content_hash or ""))
            if not res.has_more:
                break
            res = self.dbx.files_list_folder_continue(res.cursor)
        return itens

    def existe(self, caminho: str) -> bool:
        try:
            self.dbx.files_get_metadata(caminho)
            return True
        except ApiError as e:
            if e.error.is_path() and e.error.get_path().is_not_found():
                return False
            raise

    def hash_de(self, caminho: str) -> str | None:
        """content_hash do arquivo (igual para arquivos idênticos); None se não existir."""
        try:
            meta = self.dbx.files_get_metadata(caminho)
        except ApiError as e:
            if e.error.is_path() and e.error.get_path().is_not_found():
                return None
            raise
        return getattr(meta, "content_hash", None)

    def apagar(self, caminho: str) -> None:
        """Manda para a lixeira do Dropbox (recuperável); nunca apaga permanentemente."""
        self.dbx.files_delete_v2(caminho)

    def estavel(self, arq: ArquivoRemoto, segundos: int) -> bool:
        """Arquivo só é processado se não foi modificado nos últimos N segundos
        (evita ler arquivo ainda sincronizando/escaneando)."""
        return (time.time() - arq.modificado) >= segundos and arq.tamanho > 0

    def baixar(self, caminho: str) -> bytes:
        _, resp = self.dbx.files_download(caminho)
        return resp.content

    def mover(self, origem: str, destino: str) -> str:
        """Move criando pastas; se já existir arquivo com o mesmo nome, acrescenta sufixo."""
        return self._transferir(self.dbx.files_move_v2, origem, destino)

    def copiar(self, origem: str, destino: str) -> str:
        """Copia criando pastas (o original fica onde está); se o nome já existir, acrescenta sufixo."""
        return self._transferir(self.dbx.files_copy_v2, origem, destino)

    def _transferir(self, operacao, origem: str, destino: str) -> str:
        pasta_destino = destino.rsplit("/", 1)[0]
        self.garantir_pasta_recursiva(pasta_destino)
        final = destino
        tentativa = 1
        while True:
            try:
                meta = operacao(origem, final, autorename=False)
                return meta.metadata.path_display
            except ApiError as e:
                if e.error.is_to() and e.error.get_to().is_conflict():
                    tentativa += 1
                    base, _, ext = destino.rpartition(".")
                    final = f"{base}_{tentativa}.{ext}" if base else f"{destino}_{tentativa}"
                    continue
                raise

    def enviar(self, caminho: str, conteudo: bytes) -> None:
        # o upload já cria as pastas intermediárias
        self.dbx.files_upload(conteudo, caminho, mode=WriteMode.overwrite)

    def garantir_pasta_recursiva(self, caminho: str) -> None:
        # create_folder_v2 já cria as pastas intermediárias (1 chamada em vez de uma por nível)
        if caminho.strip("/"):
            self.garantir_pasta(caminho)
