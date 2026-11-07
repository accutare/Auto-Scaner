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
    sys.modules["dropbox.files"].FolderMetadata = object
    sys.modules["dropbox.files"].WriteMode = types.SimpleNamespace(overwrite=None)

import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import date

from app import servicos as servmod
from app.catalogo import interpretar_nome_pasta, montar_catalogo, normalizar
from app.dropbox_client import ArquivoRemoto
from app.extractor import ESQUEMA, interpretar_resposta
from app.main import Contexto, processar_arquivo
from app.organizador import escolher_trimestre_vat
from app.registro import Registro

RAIZ = "/Accurate/Client Data"

# Estrutura parecida com a do vídeo
ARVORE = {
    RAIZ: ["UK - VAT MTD Ready", "UK - Ltd Companies (Individual clients)", "UK - Payroll",
           "UK - Self-Assessment", "Ireland - Clients"],
    f"{RAIZ}/UK - VAT MTD Ready": [
        "!!Bridging Infomation and Templates", "!VAT - bkkg",
        "Alves Marketing Business and Network Ltd TA FGV", "Bliss Clean Group Ltd",
    ],
    f"{RAIZ}/UK - Ltd Companies (Individual clients)": [
        "!!!Year end accounts",
        "Alves Marketing Business and Network Ltd - Rafael Alves Pinto",
        "Bliss Clean Group Ltd - Fleur Thompson",
        "Escobar Cafe ( Juan Da Silva Escobar )",
    ],
    f"{RAIZ}/UK - Payroll": ["Alves Marketing Business and Network Limited"],
    f"{RAIZ}/UK - Self-Assessment": ["Rafael Alves Pinto"],
    f"{RAIZ}/Ireland - Clients": [],
    f"{RAIZ}/UK - VAT MTD Ready/Alves Marketing Business and Network Ltd TA FGV/VAT's": [
        "VAT Qe 31.03.2026", "VAT Qe 30.06.2026",
    ],
}


def listar_pastas(caminho):
    return sorted(ARVORE.get(caminho, []))


class FakeCfg:
    confianca_minima = 0.85
    pasta_revisar = "/Docflow/Revisar"
    pasta_erros = "/Docflow/Erros"
    simulacao = False


class FakeDbx:
    def __init__(self, arquivos):
        self.arquivos = dict(arquivos)
        self.movimentos = []

    def baixar(self, caminho):
        return self.arquivos[caminho]

    def mover(self, origem, destino):
        self.arquivos[destino] = self.arquivos.pop(origem)
        self.movimentos.append((origem, destino))
        return destino

    listar_pastas = staticmethod(listar_pastas)


class FakeExtrator:
    def __init__(self, resposta):
        self.resposta = resposta
        self.sistemas = []

    def extrair(self, nome, conteudo, sistema):
        self.sistemas.append(sistema)
        return interpretar_resposta(self.resposta)


def resp(**kw):
    base = {
        "legivel": True, "tipo_documento": "purchase_invoice", "resumo": "Fatura de consultoria",
        "nome_cliente_no_documento": "Alves Marketing Business & Network Limited",
        "company_number": None, "vat_number": None, "utr": None, "paye_ref": None,
        "cliente_id": None, "servico": "UK - VAT MTD Ready", "categoria": "Suppliers",
        "motivo_classificacao": "fatura de fornecedor de cliente com VAT",
        "fornecedor_ou_emissor": "Smart Growth Consultancy Ltd", "data_documento": "2026-05-14",
        "periodo_inicio": None, "periodo_fim": None, "valor_total": "1,234.56", "moeda": "GBP",
        "numero_documento": "1206", "observacoes": None, "confianca": 0.95,
    }
    base.update(kw)
    return "```json\n" + json.dumps(base) + "\n```"


class TestCatalogo(unittest.TestCase):
    def setUp(self):
        self.cat = montar_catalogo(RAIZ, listar_pastas, [
            {"pasta": "Bliss Clean Group Ltd", "company_number": "12345678", "vat_number": "GB 123 4567 89"},
        ])

    def test_nome_de_pasta(self):
        n = interpretar_nome_pasta("Alves Marketing Business and Network Ltd TA FGV")
        self.assertEqual((n.empresa, n.nome_comercial), ("Alves Marketing Business and Network Ltd", "FGV"))
        n = interpretar_nome_pasta("Escobar Cafe ( Juan Da Silva Escobar )")
        self.assertEqual((n.empresa, n.contato), ("Escobar Cafe", "Juan Da Silva Escobar"))
        self.assertEqual(normalizar("Bournemouth Carpentry & Construction LTD"),
                         normalizar("Bournemouth Carpentry and Construction Limited"))

    def test_junta_o_mesmo_cliente_de_varios_servicos(self):
        alves = [c for c in self.cat.clientes.values() if c.nome.startswith("Alves")]
        self.assertEqual(len(alves), 1)
        self.assertEqual(set(alves[0].pastas), {
            "UK - VAT MTD Ready", "UK - Ltd Companies (Individual clients)", "UK - Payroll"})
        self.assertIn("Rafael Alves Pinto", alves[0].contatos)
        self.assertIn("FGV", alves[0].nomes_comerciais)

    def test_ignora_pastas_internas_e_lista_estavel(self):
        nomes = [c.nome for c in self.cat.clientes.values()]
        self.assertFalse(any(n.startswith("!") for n in nomes))
        self.assertEqual(self.cat.texto_para_ia(), montar_catalogo(RAIZ, listar_pastas, [
            {"pasta": "Bliss Clean Group Ltd", "company_number": "12345678",
             "vat_number": "GB 123 4567 89"}]).texto_para_ia())

    def test_identificador_extra(self):
        c = self.cat.por_identificador("vat_number", "GB123456789")
        self.assertEqual(c.nome, "Bliss Clean Group Ltd")


class TestTrimestreVat(unittest.TestCase):
    PASTAS = ["VAT Qe 31.03.2026", "VAT Qe 30.06.2026"]

    def test_usa_pasta_existente(self):
        self.assertEqual(escolher_trimestre_vat(self.PASTAS, "VAT Qe ", date(2026, 5, 14)), "VAT Qe 30.06.2026")
        self.assertEqual(escolher_trimestre_vat(self.PASTAS, "VAT Qe ", date(2026, 3, 31)), "VAT Qe 31.03.2026")

    def test_cria_proximo_trimestre_no_ciclo_do_cliente(self):
        self.assertEqual(escolher_trimestre_vat(self.PASTAS, "VAT Qe ", date(2026, 8, 2)), "VAT Qe 30.09.2026")
        self.assertEqual(escolher_trimestre_vat(["VAT Qe 31.01.2026"], "VAT Qe ", date(2026, 12, 5)),
                         "VAT Qe 31.01.2027")

    def test_sem_historico_nao_adivinha(self):
        self.assertIsNone(escolher_trimestre_vat([], "VAT Qe ", date(2026, 5, 1)))


class TestFluxo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg = Registro(os.path.join(self.tmp, "t.db"))
        cat = montar_catalogo(RAIZ, listar_pastas, [])
        self.ctx = Contexto(cat, servmod.carregar(None))
        self.alves = next(c.id for c in cat.clientes.values() if c.nome.startswith("Alves"))

    def rodar(self, resposta, conteudo=b"%PDF-1.4 teste", nome="scan001.pdf", cfg=FakeCfg):
        dbx = FakeDbx({f"/Docflow/Entrada/{nome}": conteudo})
        arq = ArquivoRemoto(f"/Docflow/Entrada/{nome}", nome, len(conteudo), time.time() - 999, "h")
        processar_arquivo(arq, cfg, dbx, FakeExtrator(resposta), self.reg, self.ctx)
        return dbx

    def destino(self, dbx):
        return dbx.movimentos[0][1]

    def test_prompt_tem_catalogo_e_regras(self):
        ext = FakeExtrator(resp())
        dbx = FakeDbx({"/Docflow/Entrada/a.pdf": b"x"})
        processar_arquivo(ArquivoRemoto("/Docflow/Entrada/a.pdf", "a.pdf", 1, 0, "h"),
                          FakeCfg, dbx, ext, self.reg, self.ctx)
        self.assertIn(self.alves, ext.sistemas[0])
        self.assertIn('"UK - VAT MTD Ready"', ext.sistemas[0])
        self.assertIn('categoria "Suppliers"', ext.sistemas[0])

    def test_vat_vai_para_trimestre_e_categoria(self):
        d = self.destino(self.rodar(resp(cliente_id=self.alves)))
        self.assertTrue(d.startswith(
            f"{RAIZ}/UK - VAT MTD Ready/Alves Marketing Business and Network Ltd TA FGV/"
            "VAT's/VAT Qe 30.06.2026/Suppliers/2026-05-14 - Purchase invoice - Smart Growth"), d)
        self.assertIn("GBP 1234.56", d)
        self.assertEqual(self.reg.pendentes_de_envio()[0]["servico"], "UK - VAT MTD Ready")

    def test_mesmo_cliente_outro_servico(self):
        d = self.destino(self.rodar(resp(cliente_id=self.alves, tipo_documento="payslip",
                                         servico="UK - Payroll", categoria=None)))
        self.assertTrue(d.startswith(
            f"{RAIZ}/UK - Payroll/Alves Marketing Business and Network Limited/!Scanned docs/"), d)

    def test_servico_que_o_cliente_nao_tem_vai_para_revisar(self):
        d = self.destino(self.rodar(resp(cliente_id=self.alves, servico="UK - Self-Assessment")))
        self.assertTrue(d.startswith("/Docflow/Revisar/"))
        motivo = sqlite3.connect(self.reg.caminho_db).execute("SELECT motivo FROM documentos").fetchone()[0]
        self.assertIn("não tem pasta no serviço", motivo)

    def test_cliente_desconhecido_vai_para_revisar(self):
        self.assertTrue(self.destino(self.rodar(resp(cliente_id="C9999"))).startswith("/Docflow/Revisar/"))

    def test_confianca_baixa_vai_para_revisar(self):
        d = self.destino(self.rodar(resp(cliente_id=self.alves, confianca=0.4)))
        self.assertTrue(d.startswith("/Docflow/Revisar/"))

    def test_categoria_invalida_vai_para_revisar(self):
        d = self.destino(self.rodar(resp(cliente_id=self.alves, categoria="Qualquer")))
        self.assertTrue(d.startswith("/Docflow/Revisar/"))

    def test_vat_sem_historico_de_trimestre_vai_para_revisar(self):
        bliss = next(c.id for c in self.ctx.catalogo.clientes.values() if c.nome.startswith("Bliss"))
        d = self.destino(self.rodar(resp(cliente_id=bliss)))
        self.assertTrue(d.startswith("/Docflow/Revisar/"))

    def test_duplicado_por_conteudo(self):
        self.rodar(resp(cliente_id=self.alves))
        self.assertIn("DUPLICADO", self.destino(self.rodar(resp(cliente_id=self.alves))))

    def test_simulacao_nao_move_nada(self):
        class CfgSim(FakeCfg):
            simulacao = True
        dbx = self.rodar(resp(cliente_id=self.alves), cfg=CfgSim)
        self.assertEqual(dbx.movimentos, [])
        linha = sqlite3.connect(self.reg.caminho_db).execute(
            "SELECT status, caminho_final, motivo FROM documentos").fetchone()
        self.assertEqual(linha[0], "SIMULADO")
        self.assertIn("VAT Qe 30.06.2026/Suppliers", linha[1])
        self.assertIn("seria OK", linha[2])
        # depois de ligar de verdade, o mesmo arquivo não conta como duplicado
        self.assertTrue(self.destino(self.rodar(resp(cliente_id=self.alves))).startswith(RAIZ))

    def test_backup_e_excel(self):
        self.rodar(resp(cliente_id=self.alves))
        copia = self.reg.copia_backup()
        self.assertTrue(copia.startswith(b"SQLite format 3"))
        self.assertTrue(self.reg.exportar_excel(self.ctx.catalogo).startswith(b"PK"))

    def test_monitor_nunca_derruba_o_programa(self):
        from app.monitor import ping_falha, ping_ok
        ping_ok("")
        ping_ok("http://127.0.0.1:9/inexistente")
        ping_falha("http://127.0.0.1:9/inexistente", "teste")


class TestInterpretacao(unittest.TestCase):
    def test_valores(self):
        self.assertEqual(interpretar_resposta(resp(valor_total="£1,234.56")).valor_total, 1234.56)
        self.assertEqual(interpretar_resposta(resp(valor_total="1.234,56")).valor_total, 1234.56)
        self.assertEqual(interpretar_resposta(resp(valor_total=99.9)).valor_total, 99.9)

    def test_data_invalida_vira_nula(self):
        self.assertIsNone(interpretar_resposta(resp(data_documento="14/05/2026")).data_documento)

    def test_esquema_exige_todos_os_campos(self):
        self.assertEqual(set(ESQUEMA["required"]), set(ESQUEMA["properties"]))
        self.assertFalse(ESQUEMA["additionalProperties"])


if __name__ == "__main__":
    unittest.main()


class TestMigracao(unittest.TestCase):
    ARV = {
        "/CD": ["!Old", "Brazil - Clients", "UK - !!!Compliance", "UK - Ltd Companies (Individual clients)",
                "UK - Ltd Companies (Group clients)", "UK - VAT MTD Ready", "UK - VIP ONLY Clients"],
        "/CD/UK - !!!Compliance": ["2 GET Ltd", "31 Burnaby Rd Management Ltd"],
        "/CD/UK - Ltd Companies (Individual clients)": [
            "2Get Ltd - Soraya", "31 Burnaby Road Management Ltd - John", "Alves Marketing Ltd - Rafael"],
        "/CD/UK - Ltd Companies (Group clients)": ["Alves Marketing Limited"],
        "/CD/UK - VAT MTD Ready": ["Alves Marketing Ltd TA FGV", "Bliss Clean Group Ltd"],
        "/CD/UK - VIP ONLY Clients": [],
        "/CD/Brazil - Clients": ["Padaria Pao Quente Ltda"],
        "/CD/Brazil - Clients/x": [],
        "/CD/UK - VAT MTD Ready/y": [],
    }

    def setUp(self):
        from app.catalogo import montar_catalogo
        self.cat = montar_catalogo("/CD", lambda p: sorted(self.ARV.get(p, [])), [])

    def test_junta_espacos_e_abreviacoes(self):
        from app.catalogo import chave_cliente
        self.assertEqual(chave_cliente("2 GET Ltd"), chave_cliente("2Get Limited"))
        self.assertEqual(chave_cliente("31 Burnaby Rd Management Ltd"),
                         chave_cliente("31 Burnaby Road Management Ltd"))
        nomes = sorted(c.nome for c in self.cat.clientes.values())
        self.assertEqual(len(nomes), 5, nomes)

    def test_plano(self):
        from app.migracao import montar_plano
        linhas = montar_plano(self.cat)
        por_atual = {l.pasta_atual: l for l in linhas}
        l = por_atual["/CD/UK - VAT MTD Ready/Alves Marketing Ltd TA FGV"]
        self.assertEqual(l.pasta_nova, "/CD/Accurate UK/Alves Marketing Ltd/VAT")
        l = por_atual["/CD/Brazil - Clients/Padaria Pao Quente Ltda"]
        self.assertEqual(l.pasta_nova, "/CD/Accurate Brasil/Padaria Pao Quente Ltda/General")
        # Individual + Group do mesmo cliente caem no mesmo destino: o segundo ganha (2) e um aviso
        ltd = [x for x in linhas if x.cliente == "Alves Marketing Ltd" and "Ltd Company" in x.pasta_nova]
        self.assertEqual(sorted(x.pasta_nova.endswith("(2)") for x in ltd), [False, True])
        self.assertTrue(all(x.pasta_atual.startswith("/CD/") for x in linhas))
        self.assertFalse(any("!Old" in x.pasta_atual for x in linhas))

    def test_planilha_e_duplicados(self):
        from app.migracao import gerar_planilha, montar_plano, possiveis_duplicados
        pares = possiveis_duplicados(self.cat)
        dados = gerar_planilha(self.cat, montar_plano(self.cat), pares)
        self.assertTrue(dados.startswith(b"PK"))
