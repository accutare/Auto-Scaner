"""Typed commands from the scanner operator (the app speaks English).

Examples that all work:
    move 5715 to AN1000 / AN1000BL / 11
    5715 > AN1000 > AN1000BL > 11
    5715 5716 5720 to Bizisc Ltd / Letters
    move all to Bizisc letters
    to AN1000 / AN1000BL / 11            (uses the scans ticked in the list)

Numbers before the destination are the last digits of scan file names. The destination is a path of
existing folders, by code (AN1000 matches "AN1000 - Adalberto Neto") or by name words, separated by
"/" or ">". Folders do not need to be contiguous: "Bizisc / Letters" finds ".../AN1000BL-Bizisc Ltd/6-Letters".
Nothing here moves anything: it only works out WHAT would be moved and WHERE. No folder is ever created.
"""
import os
import re
import unicodedata
from dataclasses import dataclass, field

SEPARA = re.compile(r"\s*(?:>|/|\\)\s*")
DIVISAO = re.compile(r"(?:^|\s+)(?:to|into|pra|para)\s+|\s*>\s*", re.I)
PREFIXO = re.compile(r"^[A-Za-z]{0,4}\d+[A-Za-z]{0,4}(?![A-Za-z0-9])[\s\-_.]*")
CODIGO = re.compile(r"[A-Za-z]{0,4}\d+[A-Za-z]{0,4}")
TODOS = {"all", "everything", "todos", "tudo"}
ENCHIMENTO = {"move", "send", "put", "file", "files", "scan", "scans", "document", "documents", "doc", "docs",
              "the", "please", "and", "number", "numbers", "ending", "with", "in", "selected", "these", "this",
              "them", "it", "one", "ones", "manda", "mover", "move-to", "o", "os", "a", "as", "e"}
INICIO_DESTINO = {"the", "folder", "folders", "pasta", "a", "o"}


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


@dataclass
class Comando:
    numeros: list[str]           # last digits of scan names
    todos: bool                  # "all"
    segmentos: list[str]         # destination, one entry per folder level given
    caminho_completo: bool = False   # pasted full path (File Explorer "C:\\Users\\...\\Dropbox\\..." or "/...")


def _limpa(tok: str) -> str:
    return tok.strip(",.;:!?").lower()


def interpretar(texto: str) -> Comando | None:
    t = (texto or "").strip()
    for a, b in (("->", ">"), ("=>", ">"), ("›", ">"), ("»", ">")):
        t = t.replace(a, b)
    m = None if "\\" in t else DIVISAO.search(t)
    if "\\" in t:
        # pasted from File Explorer: numbers/words first, then the path (folder names may contain " to ")
        toks = t.split()
        i = 0
        while i < len(toks) and (_limpa(toks[i]).isdigit() or _limpa(toks[i]) in ENCHIMENTO | TODOS | {"to", "into"}):
            i += 1
        esq, dir_ = " ".join(toks[:i]), " ".join(toks[i:])
    elif m:
        esq, dir_ = t[:m.start()], t[m.end():]
        esq_toks = [_limpa(x) for x in esq.split()]
        sobra = [x for x in esq_toks if x and not x.isdigit() and x not in ENCHIMENTO | TODOS]
        if sobra and m.group(0).strip() == ">":
            # "AN1000 > AN1000BL > 11": everything is the destination (scans come from the ticked list)
            esq, dir_ = "", t
    else:
        toks = t.split()
        i = 0
        while i < len(toks) and (_limpa(toks[i]).isdigit() or _limpa(toks[i]) in ENCHIMENTO | TODOS):
            i += 1
        esq, dir_ = " ".join(toks[:i]), " ".join(toks[i:])
    esq_toks = [_limpa(x) for x in esq.split()]
    numeros = [x for x in esq_toks if x.isdigit()]
    todos = any(x in TODOS for x in esq_toks)
    segmentos = [s.strip(" ,.;:") for s in SEPARA.split(dir_.strip()) if s.strip(" ,.;:")]
    if segmentos:
        palavras = segmentos[0].split()
        while palavras and palavras[0].lower() in INICIO_DESTINO:
            palavras.pop(0)
        segmentos[0] = " ".join(palavras)
        segmentos = [s for s in segmentos if s]
    # "AN1000 AN1000BL 11" with spaces only: every word is a code -> one level each
    if len(segmentos) == 1 and len(segmentos[0].split()) > 1 and all(CODIGO.fullmatch(w) for w in segmentos[0].split()):
        segmentos = segmentos[0].split()
    if not segmentos:
        return None
    completo = "\\" in t or dir_.strip().startswith("/")
    return Comando(numeros, todos, segmentos, completo)


# ---------------------------------------------------------------- which scans
@dataclass
class Escolha:
    ok: bool
    mensagem: str = ""
    arquivos: list = field(default_factory=list)      # ArquivoRemoto
    numero: str = ""                                  # number that matched several scans
    opcoes: list = field(default_factory=list)        # ArquivoRemoto candidates for that number


def escolher_scans(cmd: Comando, scans: list, selecionados: list[str]) -> Escolha:
    if cmd.todos:
        return Escolha(True, arquivos=list(scans)) if scans else Escolha(False, "There are no scans waiting.")
    if cmd.numeros:
        achados = []
        for n in cmd.numeros:
            casa = [s for s in scans if os.path.splitext(s.nome)[0].endswith(n)]
            if not casa:
                return Escolha(False, f"No scan in the Scans folder ends with {n}. Check the number in the file name.")
            if len(casa) > 1:
                return Escolha(False, f"{len(casa)} scans end with {n}. Which one?", numero=n, opcoes=casa)
            if casa[0] not in achados:
                achados.append(casa[0])
        return Escolha(True, arquivos=achados)
    marcados = [s for s in scans if s.caminho in set(selecionados or [])]
    if marcados:
        return Escolha(True, arquivos=marcados)
    return Escolha(False, "Which scan? Type the last digits of the file name (for example 5715), "
                          "or tick the scans in the list first.")


# ---------------------------------------------------------------- which folder
def casa_segmento(seg: str, nome: str) -> int | None:
    """How well one typed level matches one folder name: 0 best, 1 good, None no match."""
    s = seg.strip()
    if CODIGO.fullmatch(s) and re.match(rf"{re.escape(s)}(?![A-Za-z0-9])", nome.strip(), re.I):
        return 0 if len(nome.strip()) > len(s) else 1   # "AN1000 - Adalberto Neto" beats a bare "AN1000"
    ns, nn = normalizar(s).split(), normalizar(nome)
    if not ns:
        return None
    if nn == " ".join(ns) or normalizar(PREFIXO.sub("", nome.strip())) == " ".join(ns):
        return 0   # "Bizisc Ltd" == "AN1000BL-Bizisc Ltd" once the code is set aside
    palavras = nn.split()
    if all(any(p.startswith(w) for p in palavras) for w in ns):
        return 1
    return None


@dataclass
class Destino:
    ok: bool
    mensagem: str = ""
    caminho: str = ""                                # absolute Dropbox path of the folder
    opcoes: list[str] = field(default_factory=list)  # absolute paths to choose from


def resolver_destino(segmentos: list[str], indice: list[tuple[str, int, list[str]]]) -> Destino:
    """indice: (absolute path, root priority, path parts below its root). Picks the single best folder."""
    cands = []
    for caminho, prio, partes in indice:
        if not partes:
            continue
        q = casa_segmento(segmentos[-1], partes[-1])
        if q is None:
            continue
        total, j, anterior, junto, ok = q, len(partes) - 2, len(partes) - 1, True, True
        for seg in reversed(segmentos[:-1]):
            while j >= 0 and casa_segmento(seg, partes[j]) is None:
                j -= 1
            if j < 0:
                ok = False
                break
            junto = junto and j == anterior - 1
            total += casa_segmento(seg, partes[j])
            anterior, j = j, j - 1
        if ok:
            cands.append(((0 if junto else 1, total, prio), len(partes), caminho))
    if not cands and len(segmentos) == 1:
        # "Bizisc letters": every word somewhere in the path, and the last folder matches one of them
        palavras = normalizar(segmentos[0]).split()
        for caminho, prio, partes in indice:
            if not partes:
                continue
            todas = normalizar(" ".join(partes)).split()
            ultima = normalizar(partes[-1]).split()
            if all(any(p.startswith(w) for p in todas) for w in palavras) and \
                    any(any(p.startswith(w) for p in ultima) for w in palavras):
                cands.append(((1, 2, prio), len(partes), caminho))
    if not cands:
        return Destino(False, f"I couldn't find a folder for “{' / '.join(segmentos)}”. "
                              "Check the name or code, or use Choose folder.")
    cands.sort()
    if len(cands) == 1 or cands[0][0] < cands[1][0]:
        return Destino(True, caminho=cands[0][2])
    melhores = [c for c in cands if c[0] == cands[0][0]]
    return Destino(False, f"{len(melhores)} folders match “{' / '.join(segmentos)}”. Which one?",
                   opcoes=[c[2] for c in melhores[:12]])


# ---------------------------------------------------------------- full paths and live folder walk
def _nome_igual(seg: str, nome: str) -> bool:
    return seg.strip().lower() == nome.strip().lower() or normalizar(seg) == normalizar(nome)


def ancorar(segmentos: list[str], raizes: list[str]):
    """'C:/Users/x/Dropbox/!Accurate.../Client Data/AV1000.../Test' -> ('/!Accurate...', ['Client Data', ...])."""
    for i, seg in enumerate(segmentos):
        for r in raizes:
            if _nome_igual(seg, r.rstrip("/").split("/")[-1]):
                return r, segmentos[i + 1:]
    return None, None


def caminhar(raiz: str, segmentos: list[str], listar) -> Destino:
    """Walks the real Dropbox folders level by level (finds folders made after the daily map)."""
    atual = raiz.rstrip("/")
    for seg in segmentos:
        nomes = listar(atual)
        exatos = [n for n in nomes if _nome_igual(seg, n)]
        if len(exatos) == 1:
            escolha = exatos[0]
        else:
            notas = sorted((q, n) for n in nomes if (q := casa_segmento(seg, n)) is not None)
            melhores = [n for q, n in notas if q == notas[0][0]] if notas else []
            onde = atual.split("/")[-1]
            if len(melhores) != 1:
                if melhores:
                    return Destino(False, f"More than one folder inside “{onde}” matches “{seg}”. Which one?",
                                   opcoes=[f"{atual}/{n}" for n in melhores[:12]])
                return Destino(False, f"There is no folder “{seg}” inside “{onde}”. "
                                      + ("These are the folders there:" if nomes else "That folder has no subfolders."),
                               opcoes=[f"{atual}/{n}" for n in nomes[:12]])
            escolha = melhores[0]
        atual = f"{atual}/{escolha}"
    return Destino(True, caminho=atual)


def resolver(cmd: Comando, indice, raizes: list[str], listar) -> Destino:
    raiz, resto = ancorar(cmd.segmentos, raizes)
    if raiz:
        if not resto:
            return Destino(False, f"Choose a folder inside “{raiz.split('/')[-1]}”, not the main folder itself.")
        return caminhar(raiz, resto, listar)
    if cmd.caminho_completo:
        nomes = " or ".join(f"“{r.split('/')[-1]}”" for r in raizes)
        return Destino(False, f"That path is outside the folders the app can file into. It must be inside {nomes}.")
    d = resolver_destino(cmd.segmentos, indice)
    if d.ok or d.opcoes:
        return d
    for r in raizes:   # not in the map yet (new folder): look at the real folders
        w = caminhar(r, cmd.segmentos, listar)
        if w.ok or (w.opcoes and "matches" in w.mensagem):
            return w
    return d
