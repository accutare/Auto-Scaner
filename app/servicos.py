"""Regras do escritório (servicos.json): países, serviços (pastas) e tipos de documento."""
import json
import os
from dataclasses import dataclass

PADRAO_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "servicos.json")


@dataclass
class Pais:
    codigo: str     # usado pela IA (ex.: "UK")
    pasta: str      # ex.: "Accurate UK"
    prefixo: str    # ex.: "UK" -> pastas "UK - VAT"


@dataclass
class Servico:
    pasta: str                  # ex.: "Limited Company"
    descricao: str
    subpasta: str = ""          # ex.: "YE {ye}" (vazio = arquivo direto na pasta do serviço)
    empresa: bool = True        # criar esta pasta para clientes empresa
    pessoa: bool = True         # criar esta pasta para clientes pessoa física


@dataclass
class Tipo:
    nome: str
    descricao: str


@dataclass
class Regras:
    regras_gerais: list[str]
    paises: dict[str, Pais]
    servicos: list[Servico]
    tipos: list[Tipo]
    year_end_padrao: str = "31/03"

    @property
    def nomes_servicos(self) -> list[str]:
        return [s.pasta for s in self.servicos]

    @property
    def nomes_tipos(self) -> list[str]:
        return [t.nome for t in self.tipos]

    def servico(self, nome: str | None) -> Servico | None:
        return next((s for s in self.servicos if s.pasta == nome), None)

    def pastas_de_servico(self, pais: Pais, tipo_cliente: str) -> list[str]:
        """Todas as pastas de serviço que um cliente desse tipo recebe (ex.: 'UK - VAT')."""
        return [f"{pais.prefixo} - {s.pasta}" for s in self.servicos
                if (s.pessoa if tipo_cliente == "pessoa" else s.empresa)]

    def texto_para_ia(self) -> str:
        linhas = ['Países (use o código em "pais"): ' + ", ".join(self.paises)]
        linhas.append('\nServiços (use o nome exato em "servico"):')
        linhas += [f'- "{s.pasta}": {s.descricao}' for s in self.servicos]
        linhas.append('\nTipos de documento (use o nome exato em "tipo_documento"):')
        linhas += [f'- "{t.nome}": {t.descricao}' for t in self.tipos]
        return "\n".join(linhas)


def carregar(caminho: str | None) -> Regras:
    """Usa o arquivo informado (ex.: /data/servicos.json) se existir; senão o do repositório."""
    arq = caminho if caminho and os.path.exists(caminho) else PADRAO_REPO
    with open(arq, encoding="utf-8") as f:
        d = json.load(f)
    paises = {}
    for codigo, v in d.get("paises", {}).items():
        if isinstance(v, str):
            v = {"pasta": v, "prefixo": codigo}
        paises[codigo] = Pais(codigo, v["pasta"], v.get("prefixo", codigo))
    return Regras(
        regras_gerais=list(d.get("regras_gerais", [])),
        paises=paises,
        servicos=[Servico(s["pasta"], s.get("descricao", ""), s.get("subpasta", ""),
                          bool(s.get("empresa", True)), bool(s.get("pessoa", True)))
                  for s in d.get("servicos", [])],
        tipos=[Tipo(t.get("nome") or t["pasta"], t.get("descricao", "")) for t in d.get("tipos_documento", [])],
        year_end_padrao=d.get("year_end_padrao", "31/03"),
    )
