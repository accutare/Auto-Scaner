"""Registro dos documentos (SQLite) e exportação para Excel."""
import io
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

COLUNAS = [
    ("id", "ID"),
    ("recebido_em", "Recebido em"),
    ("status", "Status"),
    ("cliente", "Cliente"),
    ("cnpj_cpf_cliente", "CNPJ/CPF cliente"),
    ("tipo", "Tipo"),
    ("data_documento", "Data documento"),
    ("competencia", "Competência"),
    ("fornecedor", "Fornecedor/Emissor"),
    ("valor_total", "Valor"),
    ("numero_documento", "Nº documento"),
    ("confianca", "Confiança"),
    ("motivo", "Motivo (se pendente/erro)"),
    ("arquivo_original", "Arquivo original"),
    ("caminho_final", "Caminho final no Dropbox"),
    ("enviado_app_em", "Enviado ao aplicativo em"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recebido_em TEXT NOT NULL,
    status TEXT NOT NULL,              -- OK | PENDENTE | ERRO | ENVIADO
    cliente TEXT,
    cnpj_cpf_cliente TEXT,
    tipo TEXT,
    data_documento TEXT,
    competencia TEXT,
    fornecedor TEXT,
    valor_total REAL,
    numero_documento TEXT,
    chave_acesso TEXT,
    confianca REAL,
    motivo TEXT,
    arquivo_original TEXT,
    caminho_final TEXT,
    hash_arquivo TEXT NOT NULL,
    enviado_app_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_hash ON documentos(hash_arquivo);
CREATE INDEX IF NOT EXISTS ix_chave ON documentos(chave_acesso);
CREATE INDEX IF NOT EXISTS ix_status ON documentos(status);
"""


def agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Registro:
    def __init__(self, caminho_db: str):
        self.caminho_db = caminho_db
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.caminho_db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def duplicado(self, hash_arquivo: str, chave_acesso: str | None = None) -> bool:
        with self._conn() as c:
            r = c.execute(
                "SELECT 1 FROM documentos WHERE hash_arquivo=? AND status != 'ERRO' LIMIT 1",
                (hash_arquivo,),
            ).fetchone()
            if r:
                return True
            if chave_acesso:
                r = c.execute(
                    "SELECT 1 FROM documentos WHERE chave_acesso=? AND status != 'ERRO' LIMIT 1",
                    (chave_acesso,),
                ).fetchone()
                return bool(r)
        return False

    def inserir(self, **campos) -> int:
        campos.setdefault("recebido_em", agora())
        cols = ", ".join(campos)
        marcas = ", ".join("?" for _ in campos)
        with self._conn() as c:
            cur = c.execute(
                f"INSERT INTO documentos ({cols}) VALUES ({marcas})", tuple(campos.values())
            )
            return cur.lastrowid

    def pendentes_de_envio(self) -> list[sqlite3.Row]:
        """Documentos OK que ainda não foram enviados ao aplicativo (etapa futura)."""
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM documentos WHERE status='OK' ORDER BY id"
            ).fetchall()

    def marcar_enviado(self, doc_id: int) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE documentos SET status='ENVIADO', enviado_app_em=? WHERE id=?",
                (agora(), doc_id),
            )

    def copia_backup(self) -> bytes:
        """Cópia consistente do banco (usa a API de backup do SQLite, segura com o banco em uso)."""
        with tempfile.TemporaryDirectory() as tmp:
            destino = os.path.join(tmp, "backup.db")
            origem = sqlite3.connect(self.caminho_db)
            copia = sqlite3.connect(destino)
            try:
                origem.backup(copia)
            finally:
                copia.close()
                origem.close()
            with open(destino, "rb") as f:
                return f.read()

    def exportar_excel(self) -> bytes:
        with self._conn() as c:
            linhas = c.execute("SELECT * FROM documentos ORDER BY id DESC").fetchall()
        wb = Workbook()
        ws = wb.active
        ws.title = "Controle"
        ws.append([titulo for _, titulo in COLUNAS])
        for cel in ws[1]:
            cel.font = Font(bold=True, color="FFFFFF")
            cel.fill = PatternFill("solid", fgColor="305496")
        cores = {"PENDENTE": "FFF2CC", "ERRO": "F8CBAD", "ENVIADO": "E2EFDA"}
        for r in linhas:
            ws.append([r[chave] for chave, _ in COLUNAS])
            cor = cores.get(r["status"])
            if cor:
                for cel in ws[ws.max_row]:
                    cel.fill = PatternFill("solid", fgColor=cor)
        for i, (chave, titulo) in enumerate(COLUNAS, 1):
            largura = max(len(titulo), 12)
            ws.column_dimensions[get_column_letter(i)].width = min(largura + 6, 45)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
