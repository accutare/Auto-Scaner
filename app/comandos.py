"""Comandos da pessoa do scanner: "5715 > AN1000 > AN1000BL > 11".

O primeiro número é o final do nome do arquivo escaneado; os demais são os códigos das pastas (cada pasta
existente cujo nome COMEÇA com o código). Nada é movido sem confirmação, nenhuma pasta é criada e nada é
excluído.
"""
import re
from dataclasses import dataclass, field

from .destino_codigo import _candidatas

PALAVRAS = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


@dataclass
class Pedido:
    final: str
    codigos: list[str]


def interpretar(texto: str) -> Pedido | None:
    """Aceita "5715 > AN1000 > AN1000BL > 11", "5715 AN1000 AN1000BL 11" ou
    "manda o 5715 pra AN1000 AN1000BL 11": o primeiro número é o final do arquivo; depois, cada palavra
    com algum dígito é um código."""
    tokens = PALAVRAS.findall(texto or "")
    i = next((k for k, t in enumerate(tokens) if t.isdigit()), None)
    if i is None:
        return None
    codigos = [t for t in tokens[i + 1:] if any(ch.isdigit() for ch in t)]
    return Pedido(tokens[i], codigos) if codigos else None


@dataclass
class Resultado:
    ok: bool
    mensagem: str
    arquivo: dict | None = None            # {"caminho", "nome"}
    caminho: list[str] = field(default_factory=list)   # pastas encontradas (nomes)
    destino: str | None = None             # caminho completo da pasta de destino
    opcoes: list[str] = field(default_factory=list)    # sugestões (arquivos ou pastas)
    tipo_opcoes: str | None = None         # "arquivo" | "pasta"
    nivel: int = 0                         # em qual código parou (para sugestões de pasta)


def resolver_pedido(pedido: Pedido, scans: list, raiz: str, listar_pastas) -> Resultado:
    import os
    achados = [s for s in scans if os.path.splitext(s.nome)[0].endswith(pedido.final)]
    if not achados:
        return Resultado(False, f"Não encontrei nenhum scan terminando em {pedido.final} na pasta de scans. "
                                "Confira o número no nome do arquivo.")
    if len(achados) > 1:
        return Resultado(False, f"Tem {len(achados)} scans terminando em {pedido.final}. Qual é?",
                         opcoes=[s.nome for s in achados], tipo_opcoes="arquivo")
    arq = achados[0]
    atual, caminho = raiz.rstrip("/"), []
    for n, codigo in enumerate(pedido.codigos):
        nomes = listar_pastas(atual)
        com_descricao, exatas = _candidatas(codigo, nomes)
        escolhidas = com_descricao or exatas
        if len(escolhidas) != 1:
            onde = " / ".join(caminho) or "pasta principal"
            if escolhidas:
                msg = f"Mais de uma pasta começa com {codigo} em {onde}. Nada foi movido."
            else:
                msg = f"Não encontrei pasta que comece com {codigo} em {onde}. Nada foi movido."
            return Resultado(False, msg, arquivo={"caminho": arq.caminho, "nome": arq.nome}, caminho=caminho,
                             opcoes=(escolhidas or nomes)[:40], tipo_opcoes="pasta", nivel=n)
        caminho.append(escolhidas[0])
        atual = f"{atual}/{escolhidas[0]}"
    return Resultado(True, "ok", arquivo={"caminho": arq.caminho, "nome": arq.nome}, caminho=caminho,
                     destino=atual)
