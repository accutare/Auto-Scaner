"""Regras por serviço (servicos.json): descrição para a IA e subpasta de destino."""
import json
import os
from dataclasses import dataclass, field

PADRAO_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "servicos.json")


@dataclass
class RegraServico:
    corresponde: list[str]
    descricao: str
    subpasta: str | None = None
    categorias: dict[str, str] = field(default_factory=dict)


@dataclass
class Regras:
    regras_gerais: list[str]
    subpasta_padrao: str
    servicos: list[RegraServico]

    def para(self, pasta_servico: str) -> RegraServico | None:
        """Primeira regra cujo termo aparece no nome do serviço (parte depois de 'UK - ')."""
        nome = pasta_servico.split(" - ", 1)[-1].lower()
        for r in self.servicos:
            if any(t.lower() in nome for t in r.corresponde):
                return r
        return None

    def subpasta(self, pasta_servico: str) -> str:
        r = self.para(pasta_servico)
        return (r.subpasta if r and r.subpasta else self.subpasta_padrao).strip("/")

    def categorias(self, pasta_servico: str) -> dict[str, str]:
        r = self.para(pasta_servico)
        return dict(r.categorias) if r else {}

    def texto_para_ia(self, servicos_existentes: list[str]) -> str:
        linhas = []
        for s in servicos_existentes:
            r = self.para(s)
            desc = r.descricao if r else "(sem descrição: só escolha se for óbvio)"
            linhas.append(f'- "{s}": {desc}')
            for cat, cdesc in (r.categorias.items() if r else []):
                linhas.append(f'    categoria "{cat}": {cdesc}')
        return "\n".join(linhas)


def carregar(caminho: str | None) -> Regras:
    """Usa o arquivo informado (ex.: /data/servicos.json) se existir; senão o do repositório."""
    arq = caminho if caminho and os.path.exists(caminho) else PADRAO_REPO
    with open(arq, encoding="utf-8") as f:
        d = json.load(f)
    return Regras(
        regras_gerais=list(d.get("regras_gerais", [])),
        subpasta_padrao=d.get("subpasta_padrao") or "!Scanned docs",
        servicos=[
            RegraServico(
                corresponde=list(s.get("corresponde", [])),
                descricao=s.get("descricao", ""),
                subpasta=s.get("subpasta"),
                categorias=dict(s.get("categorias", {})),
            )
            for s in d.get("servicos", [])
        ],
    )
