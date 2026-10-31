"""Loop principal: vigia a pasta de entrada do Dropbox e distribui cada documento para o cliente/serviço."""
import hashlib
import logging
import os
import signal
import sys
import time
from datetime import datetime

from . import config as cfgmod
from . import servicos as servmod
from .catalogo import carregar_extras, montar_catalogo
from .dropbox_client import ArquivoRemoto, DropboxClient
from .extractor import Extrator, montar_sistema
from .monitor import ping_falha, ping_ok
from .organizador import decidir
from .registro import Registro

EXTENSOES = (".pdf", ".jpg", ".jpeg", ".png", ".webp")
DIAS_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")
MAX_TENTATIVAS = 3

log = logging.getLogger("docflow")
_parar = False


def _sinal(*_):
    global _parar
    _parar = True
    log.info("Sinal de parada recebido; terminando o arquivo atual...")


class Contexto:
    """O que cada documento precisa: catálogo de clientes, regras e o prompt de sistema montado."""

    def __init__(self, catalogo, regras):
        self.catalogo = catalogo
        self.regras = regras
        self.sistema = montar_sistema(
            regras.regras_gerais, regras.texto_para_ia(catalogo.servicos), catalogo.texto_para_ia()
        )


def processar_arquivo(arq: ArquivoRemoto, cfg, dbx, extrator, registro, ctx: Contexto) -> str:
    """Processa um arquivo e devolve o status gravado."""
    conteudo = dbx.baixar(arq.caminho)
    hash_arq = hashlib.sha256(conteudo).hexdigest()
    hash_curto = hash_arq[:8]

    def mover(destino):
        return destino if cfg.simulacao else dbx.mover(arq.caminho, destino)

    if registro.duplicado(hash_arq):
        final = mover(f"{cfg.pasta_revisar}/DUPLICADO [{hash_curto}] {arq.nome}")
        status = "SIMULADO" if cfg.simulacao else "PENDENTE"
        registro.inserir(
            status=status, arquivo_original=arq.nome, caminho_final=final,
            hash_arquivo=hash_arq, motivo="arquivo idêntico já processado antes (duplicado)",
        )
        log.info("Duplicado: %s", arq.nome)
        return status

    ext = extrator.extrair(arq.nome, conteudo, ctx.sistema)
    d = decidir(ext, arq.nome, ctx.catalogo, ctx.regras, cfg.confianca_minima,
                cfg.pasta_revisar, hash_curto, dbx.listar_pastas)
    final = mover(d.caminho_destino)
    status = "SIMULADO" if cfg.simulacao else d.status
    motivo = d.motivo
    if cfg.simulacao:
        motivo = f"[simulação: seria {d.status}] {d.motivo or ''}".strip()
    registro.inserir(
        status=status, cliente=d.cliente, servico=d.servico, categoria=d.categoria,
        tipo=ext.tipo, data_documento=ext.data_documento, fornecedor=ext.fornecedor_ou_emissor,
        valor_total=ext.valor_total, moeda=ext.moeda, numero_documento=ext.numero_documento,
        confianca=ext.confianca, motivo=motivo, resumo=ext.resumo, arquivo_original=arq.nome,
        caminho_final=final, hash_arquivo=hash_arq,
    )
    log.info("%s -> %s (%s)", arq.nome, status, final)
    return status


def mover_para_erros(arq, cfg, dbx, registro, motivo):
    try:
        hash_arq = hashlib.sha256(dbx.baixar(arq.caminho)).hexdigest()
    except Exception:  # noqa: BLE001
        hash_arq = arq.content_hash or arq.nome
    destino = f"{cfg.pasta_erros}/[{hash_arq[:8]}] {arq.nome}"
    final = destino if cfg.simulacao else dbx.mover(arq.caminho, destino)
    registro.inserir(
        status="SIMULADO" if cfg.simulacao else "ERRO", arquivo_original=arq.nome,
        caminho_final=final, hash_arquivo=hash_arq, motivo=f"erro técnico: {motivo[:500]}",
    )


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    cfg = cfgmod.carregar()
    os.makedirs(cfg.dados_dir, exist_ok=True)

    dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token,
                        cfg.dropbox_team_space)
    extrator = Extrator(cfg.anthropic_api_key, cfg.claude_model, cfg.claude_esforco)
    registro = Registro(os.path.join(cfg.dados_dir, "controle.db"))
    for p in (cfg.pasta_entrada, cfg.pasta_revisar, cfg.pasta_erros, cfg.pasta_controle):
        dbx.garantir_pasta_recursiva(p)

    signal.signal(signal.SIGTERM, _sinal)
    signal.signal(signal.SIGINT, _sinal)
    tentativas: dict[str, int] = {}
    ja_simulados: set[str] = set()     # em simulação o arquivo fica na Entrada: não reprocessar
    ctx: Contexto | None = None
    ultimo_catalogo = 0.0
    ultimo_export = 0.0
    ultimo_backup = ""

    log.info("Iniciado%s. Vigiando %s a cada %ss", " em MODO SIMULAÇÃO (nada será movido)"
             if cfg.simulacao else "", cfg.pasta_entrada, cfg.intervalo_segundos)
    while not _parar:
        try:
            alterou = False
            if ctx is None or (time.time() - ultimo_catalogo) > cfg.catalogo_minutos * 60:
                catalogo = montar_catalogo(cfg.pasta_client_data, dbx.listar_pastas,
                                           carregar_extras(cfg.clientes_extra_csv))
                if not catalogo.clientes:
                    raise RuntimeError(f"Nenhum cliente encontrado em {cfg.pasta_client_data}")
                ctx = Contexto(catalogo, servmod.carregar(cfg.servicos_json))
                ultimo_catalogo = time.time()
                alterou = True
                log.info("Catálogo: %d serviços, %d clientes", len(catalogo.servicos), len(catalogo.clientes))

            for arq in dbx.listar(cfg.pasta_entrada):
                if _parar:
                    break
                if not arq.nome.lower().endswith(EXTENSOES):
                    continue
                if cfg.simulacao and arq.content_hash in ja_simulados:
                    continue
                if not dbx.estavel(arq, cfg.segundos_estabilizar):
                    continue
                try:
                    processar_arquivo(arq, cfg, dbx, extrator, registro, ctx)
                    alterou = True
                    tentativas.pop(arq.caminho, None)
                    if cfg.simulacao:
                        ja_simulados.add(arq.content_hash)
                except Exception as e:  # noqa: BLE001
                    n = tentativas.get(arq.caminho, 0) + 1
                    tentativas[arq.caminho] = n
                    log.exception("Falha (%d/%d) em %s", n, MAX_TENTATIVAS, arq.nome)
                    if n >= MAX_TENTATIVAS:
                        mover_para_erros(arq, cfg, dbx, registro, f"{type(e).__name__}: {e}")
                        tentativas.pop(arq.caminho, None)
                        if cfg.simulacao:
                            ja_simulados.add(arq.content_hash)
                        alterou = True
            # Atualiza o Controle.xlsx no Dropbox (somente leitura: a fonte é o banco)
            if alterou or (time.time() - ultimo_export) > 3600:
                dbx.enviar(f"{cfg.pasta_controle}/Controle.xlsx",
                           registro.exportar_excel(ctx.catalogo if ctx else None))
                ultimo_export = time.time()
            # Backup do banco 1x por dia (7 cópias rotativas: uma por dia da semana)
            hoje = datetime.now().strftime("%Y-%m-%d")
            if hoje != ultimo_backup:
                dia = DIAS_SEMANA[datetime.now().weekday()]
                dbx.enviar(f"{cfg.pasta_controle}/backup/controle-{dia}.db", registro.copia_backup())
                ultimo_backup = hoje
                log.info("Backup diário enviado (%s)", dia)
            ping_ok(cfg.healthcheck_url)
        except Exception as e:  # noqa: BLE001
            log.exception("Erro no ciclo; tentará de novo")
            ping_falha(cfg.healthcheck_url, f"{type(e).__name__}: {e}")
        for _ in range(cfg.intervalo_segundos):
            if _parar:
                break
            time.sleep(1)
    log.info("Encerrado.")


if __name__ == "__main__":
    main()
