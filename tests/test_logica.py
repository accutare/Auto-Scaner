"""Testes da lógica, sem Dropbox nem IA reais (usa dublês)."""
import sys
import types

# O ambiente de teste pode não ter os pacotes reais: cria dublês só para permitir os imports.
for _nome in ("dropbox", "dropbox.exceptions", "dropbox.files", "anthropic"):
    if _nome not in sys.modules:
        try:
            __import__(_nome)
        except ImportError:
            sys.modules[_nome] = types.ModuleType(_nome)
if not hasattr(sys.modules["dropbox.exceptions"], "ApiError"):
    sys.modules["dropbox.exceptions"].ApiError = Exception
if not hasattr(sys.modules["dropbox.files"], "FileMetadata"):
    sys.modules["dropbox.files"].FileMetadata = object
    sys.modules["dropbox.files"].WriteMode = types.SimpleNamespace(overwrite=None)

import json
import os
import tempfile
import time
import unittest

from app.dropbox_client import ArquivoRemoto
from app.extractor import interpretar_resposta
from app.main import processar_arquivo
from app.organizador import carregar_clientes
from app.registro import Registro

PASTAS = {
    "entrada": "/Entrada", "processados": "/Processados",
    "revisar": "/Revisar", "erros": "/Erros",
}


class FakeCfg:
    confianca_minima = 0.85


class FakeDbx:
    def __init__(self, arquivos):
        self.arquivos = dict(arquivos)  # caminho -> bytes
        self.movimentos = []

    def baixar(self, caminho):
        return self.arquivos[caminho]

    def mover(self, origem, destino):
        self.arquivos[destino] = self.arquivos.pop(origem)
        self.movimentos.append((origem, destino))
        return destino


class FakeExtrator:
    def __init__(self, resposta):
        self.resposta = resposta

    def extrair(self, nome, conteudo):
        return interpretar_resposta(self.resposta)


def resp(**kw):
    base = {
        "tipo": "nota_fiscal", "cnpj_cpf_cliente": "11.222.333/0001-81",
        "nome_cliente": "Empresa Exemplo", "fornecedor_ou_emissor": "Papelaria São João Ltda",
        "cnpj_cpf_fornecedor": "99888777000166", "data_documento": "2026-10-15",
        "competencia": "2026-10", "valor_total": "1.234,56", "numero_documento": "123",
        "chave_acesso": None, "observacoes": None, "confianca": 0.95, "legivel": True,
    }
    base.update(kw)
    return "```json\n" + json.dumps(base) + "\n```"


class Testes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg = Registro(os.path.join(self.tmp, "t.db"))
        csv_path = os.path.join(self.tmp, "c.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("cnpj_cpf;nome\n11.222.333/0001-81;Empresa Exemplo Ltda\n")
        self.clientes = carregar_clientes(csv_path)

    def rodar(self, resposta, conteudo=b"%PDF-1.4 teste", nome="scan001.pdf"):
        dbx = FakeDbx({f"/Entrada/{nome}": conteudo})
        arq = ArquivoRemoto(f"/Entrada/{nome}", nome, len(conteudo), time.time() - 999, "h")
        processar_arquivo(arq, FakeCfg, dbx, FakeExtrator(resposta), self.reg, self.clientes, PASTAS)
        return dbx

    def test_interpretacao_valor_br_e_cnpj(self):
        e = interpretar_resposta(resp())
        self.assertEqual(e.valor_total, 1234.56)
        self.assertEqual(e.cnpj_cpf_cliente, "11222333000181")

    def test_ok_vai_para_processados(self):
        dbx = self.rodar(resp())
        destino = dbx.movimentos[0][1]
        self.assertTrue(destino.startswith("/Processados/Empresa_Exemplo_Ltda/2026/10/nota_fiscal/"))
        self.assertIn("1234,56", destino)
        linha = self.reg.pendentes_de_envio()[0]
        self.assertEqual(linha["status"], "OK")

    def test_cliente_desconhecido_vai_para_revisar(self):
        dbx = self.rodar(resp(cnpj_cpf_cliente="00000000000000", cnpj_cpf_fornecedor="11111111111111"))
        self.assertTrue(dbx.movimentos[0][1].startswith("/Revisar/"))

    def test_confianca_baixa_vai_para_revisar(self):
        dbx = self.rodar(resp(confianca=0.4))
        self.assertTrue(dbx.movimentos[0][1].startswith("/Revisar/"))

    def test_duplicado_por_conteudo(self):
        self.rodar(resp())
        dbx = self.rodar(resp())
        self.assertIn("DUPLICADO_", dbx.movimentos[0][1])

    def test_duplicado_por_chave_nfe(self):
        chave = "3" * 44
        self.rodar(resp(chave_acesso=chave), conteudo=b"arquivo A")
        dbx = self.rodar(resp(chave_acesso=chave), conteudo=b"arquivo B diferente")
        self.assertIn("DUPLICADO_", dbx.movimentos[0][1])

    def test_backup_do_banco_e_restauravel(self):
        import sqlite3
        self.rodar(resp())
        copia = self.reg.copia_backup()
        self.assertTrue(copia.startswith(b"SQLite format 3"))
        destino = os.path.join(self.tmp, "restaurado.db")
        with open(destino, "wb") as f:
            f.write(copia)
        n = sqlite3.connect(destino).execute("SELECT COUNT(*) FROM documentos").fetchone()[0]
        self.assertEqual(n, 1)

    def test_monitor_nunca_derruba_o_programa(self):
        from app.monitor import ping_falha, ping_ok
        ping_ok("")  # sem URL: não faz nada
        ping_ok("http://127.0.0.1:9/inexistente")   # porta fechada: apenas registra aviso
        ping_falha("http://127.0.0.1:9/inexistente", "teste")

    def test_exporta_excel(self):
        self.rodar(resp())
        self.assertTrue(self.reg.exportar_excel().startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
