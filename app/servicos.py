"""Regras do escritório (servicos.json): países, serviços e tipos de documento que viram pastas."""
import json
import os
from dataclasses import dataclass

PADRAO_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "servicos.json")


@dataclass
class Item:
    pasta: str
    descricao: str
    arquivo: str = ""   # rótulo usado no nome do arquivo (tipos de documento)


@dataclass
class Regras:
    regras_gerais: list[str]
    paises: dict[str, str]          # código usado pela IA -> nome da pasta do país
    servicos: list[Item]
    tipos: list[Item]

    @property
    def nomes_servicos(self) -> list[str]:
        return [s.pasta for s in self.servicos]

    @property
    def nomes_tipos(self) -> list[str]:
        return [t.pasta for t in self.tipos]

    def rotulo_tipo(self, pasta_tipo: str | None) -> str:
        for t in self.tipos:
            if t.pasta == pasta_tipo:
                return t.arquivo or t.pasta
        return "Document"

    def texto_para_ia(self) -> str:
        linhas = ["Países (use o código em \"pais\"): " + ", ".join(self.paises)]
        linhas.append("\nServiços (use o nome exato em \"servico\"):")
        linhas += [f'- "{s.pasta}": {s.descricao}' for s in self.servicos]
        linhas.append("\nTipos de documento (use o nome exato em \"tipo_documento\"):")
        linhas += [f'- "{t.pasta}": {t.descricao}' for t in self.tipos]
        return "\n".join(linhas)


def carregar(caminho: str | None) -> Regras:
    """Usa o arquivo informado (ex.: /data/servicos.json) se existir; senão o do repositório."""
    arq = caminho if caminho and os.path.exists(caminho) else PADRAO_REPO
    with open(arq, encoding="utf-8") as f:
        d = json.load(f)
    return Regras(
        regras_gerais=list(d.get("regras_gerais", [])),
        paises=dict(d.get("paises", {})),
        servicos=[Item(s["pasta"], s.get("descricao", "")) for s in d.get("servicos", [])],
        tipos=[Item(t["pasta"], t.get("descricao", ""), t.get("arquivo", "")) for t in d.get("tipos_documento", [])],
    )
