"""Loop principal: vigia a pasta de entrada do Dropbox e processa cada arquivo."""
import hashlib
import logging
import os
import signal
import sys
import time
from datetime import datetime

from . import config as cfgmod
from .dropbox_client import DropboxClient, ArquivoRemoto
from .extractor import Extrator
from .monitor import ping_falha, ping_ok
from .organizador import carregar_clientes, decidir
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


def processar_arquivo(arq: ArquivoRemoto, cfg, dbx, extrator, registro, clientes, pastas):
    conteudo = dbx.baixar(arq.caminho)
    hash_arq = hashlib.sha256(conteudo).hexdigest()
    hash_curto = hash_arq[:8]

    if registro.duplicado(hash_arq):
        destino = f"{pastas['revisar']}/DUPLICADO_{hash_curto}_{arq.nome}"
        final = dbx.mover(arq.caminho, destino)
        registro.inserir(
            status="PENDENTE", arquivo_original=arq.nome, caminho_final=final,
            hash_arquivo=hash_arq, motivo="arquivo idêntico já processado antes (duplicado)",
        )
        log.info("Duplicado: %s", arq.nome)
        return

    ext = extrator.extrair(arq.nome, conteudo)

    # Duplicidade por chave de acesso da NF-e (mesmo documento em arquivos diferentes)
    if ext.chave_acesso and registro.duplicado("-", ext.chave_acesso):
        destino = f"{pastas['revisar']}/DUPLICADO_{hash_curto}_{arq.nome}"
        final = dbx.mover(arq.caminho, destino)
        registro.inserir(
            status="PENDENTE", arquivo_original=arq.nome, caminho_final=final,
            hash_arquivo=hash_arq, chave_acesso=ext.chave_acesso,
            motivo="nota fiscal com a mesma chave de acesso já registrada (duplicado)",
        )
        log.info("Duplicado por chave NF-e: %s", arq.nome)
        return

    d = decidir(ext, arq.nome, clientes, cfg.confianca_minima, pastas, hash_curto)
    final = dbx.mover(arq.caminho, d.caminho_destino)
    registro.inserir(
        status=d.status, cliente=d.cliente, cnpj_cpf_cliente=ext.cnpj_cpf_cliente,
        tipo=ext.tipo, data_documento=ext.data_documento, competencia=ext.competencia,
        fornecedor=ext.fornecedor_ou_emissor, valor_total=ext.valor_total,
        numero_documento=ext.numero_documento, chave_acesso=ext.chave_acesso,
        confianca=ext.confianca, motivo=d.motivo, arquivo_original=arq.nome,
        caminho_final=final, hash_arquivo=hash_arq,
    )
    log.info("%s -> %s (%s)", arq.nome, d.status, final)


def mover_para_erros(arq, dbx, registro, pastas, motivo):
    try:
        conteudo = dbx.baixar(arq.caminho)
        hash_arq = hashlib.sha256(conteudo).hexdigest()
    except Exception:
        hash_arq = arq.content_hash or arq.nome
    final = dbx.mover(arq.caminho, f"{pastas['erros']}/{hash_arq[:8]}_{arq.nome}")
    registro.inserir(
        status="ERRO", arquivo_original=arq.nome, caminho_final=final,
        hash_arquivo=hash_arq, motivo=motivo[:500],
    )


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    cfg = cfgmod.carregar()
    os.makedirs(cfg.dados_dir, exist_ok=True)

    dbx = DropboxClient(cfg.dropbox_app_key, cfg.dropbox_app_secret, cfg.dropbox_refresh_token)
    extrator = Extrator(cfg.anthropic_api_key, cfg.claude_model)
    registro = Registro(os.path.join(cfg.dados_dir, "controle.db"))
    pastas = {
        "entrada": cfg.pasta_entrada, "processados": cfg.pasta_processados,
        "revisar": cfg.pasta_revisar, "erros": cfg.pasta_erros,
    }
    for p in pastas.values():
        dbx._garantir_pasta_recursiva(p)

    signal.signal(signal.SIGTERM, _sinal)
    signal.signal(signal.SIGINT, _sinal)
    tentativas: dict[str, int] = {}
    ultimo_export = 0.0
    ultimo_backup = ""

    log.info("Iniciado. Vigiando %s a cada %ss", cfg.pasta_entrada, cfg.intervalo_segundos)
    while not _parar:
        try:
            clientes = carregar_clientes(cfg.clientes_csv)
            alterou = False
            for arq in dbx.listar(cfg.pasta_entrada):
                if _parar:
                    break
                if not arq.nome.lower().endswith(EXTENSOES):
                    continue
                if not dbx.estavel(arq, cfg.segundos_estabilizar):
                    continue
                try:
                    processar_arquivo(arq, cfg, dbx, extrator, registro, clientes, pastas)
                    alterou = True
                except Exception as e:  # noqa: BLE001
                    n = tentativas.get(arq.caminho, 0) + 1
                    tentativas[arq.caminho] = n
                    log.exception("Falha (%d/%d) em %s", n, MAX_TENTATIVAS, arq.nome)
                    if n >= MAX_TENTATIVAS:
                        mover_para_erros(arq, dbx, registro, pastas, f"{type(e).__name__}: {e}")
                        tentativas.pop(arq.caminho, None)
                        alterou = True
            # Atualiza o Controle.xlsx no Dropbox (somente leitura: a fonte é o banco)
            if alterou or (time.time() - ultimo_export) > 3600:
                dbx.enviar(f"{cfg.pasta_controle}/Controle.xlsx", registro.exportar_excel())
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
