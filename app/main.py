"""Lê os documentos escaneados (pasta de entrada) e organiza cada um em País/Cliente/Serviço/Tipo.

Uso:
  python -m app.main            fica ligado, olhando a pasta de entrada a cada minuto
  python -m app.main --uma-vez  processa o que estiver na pasta de entrada agora e termina (para testes)
  python -m app.main --uma-vez --limite 1   idem, mas só o arquivo mais recente

Com SIMULACAO=true nada é gravado no Dropbox: o resultado aparece só na tela.
"""
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
from .extractor import Extrator, montar_esquema, montar_sistema
from .monitor import ping_falha, ping_ok
from .destino_codigo import corrigir_pastas_literais
from .destino_codigo import resolver as resolver_destino
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
        self.sistema = montar_sistema(regras, catalogo.texto_para_ia())
        self.esquema = montar_esquema(list(regras.paises), regras.nomes_servicos, regras.nomes_tipos)


def processar_arquivo(arq: ArquivoRemoto, cfg, dbx, extrator, registro, ctx: Contexto) -> dict:
    """Processa um arquivo e devolve um resumo do que foi decidido."""
    conteudo = dbx.baixar(arq.caminho)
    hash_arq = hashlib.sha256(conteudo).hexdigest()
    hash_curto = hash_arq[:8]

    copiar = getattr(cfg, "copiar", False)

    def mover(destino):
        if cfg.simulacao:
            return destino
        return dbx.copiar(arq.caminho, destino) if copiar else dbx.mover(arq.caminho, destino)

    if registro.duplicado(hash_arq):
        if copiar:
            # No modo cópia o original fica na pasta de entrada: o que já foi copiado é só pulado.
            log.info("Já copiado antes, pulando: %s", arq.nome)
            return None
        final = mover(f"{cfg.pasta_revisar}/DUPLICADO [{hash_curto}] {arq.nome}")
        status = "SIMULADO" if cfg.simulacao else "PENDENTE"
        registro.inserir(
            status=status, arquivo_original=arq.nome, caminho_final=final,
            hash_arquivo=hash_arq, motivo="arquivo idêntico já processado antes (duplicado)",
        )
        log.info("Duplicado: %s", arq.nome)
        return {"arquivo": arq.nome, "status": status, "destino": final, "motivo": "duplicado"}

    destino_fixo = getattr(cfg, "destino_fixo", "")
    if destino_fixo:
        return _processar_destino_fixo(arq, cfg, dbx, extrator, registro, ctx, conteudo, hash_arq,
                                       hash_curto, destino_fixo, mover)

    ext = extrator.extrair(arq.nome, conteudo, ctx.sistema, ctx.esquema)
    d = decidir(ext, arq.nome, ctx.catalogo, ctx.regras, cfg.confianca_minima,
                cfg.pasta_clientes, cfg.pasta_revisar, hash_curto, dbx.listar_pastas)
    if d.status == "OK" and not cfg.simulacao:
        # Todo cliente recebe todas as pastas de serviço, mesmo vazias
        for pasta in d.pastas_criar:
            dbx.garantir_pasta(pasta)
    final = mover(d.caminho_destino)
    status = "SIMULADO" if cfg.simulacao else d.status
    motivo = d.motivo
    if cfg.simulacao:
        motivo = f"[simulação: seria {d.status}] {d.motivo or ''}".strip()
    registro.inserir(
        status=status, pais=d.pais, cliente=d.cliente, dono=ext.dono_ou_diretor, servico=d.servico,
        tipo=d.tipo, data_documento=ext.data_documento, fornecedor=ext.fornecedor_ou_emissor,
        valor_total=ext.valor_total, moeda=ext.moeda, numero_documento=ext.numero_documento,
        confianca=ext.confianca, motivo=motivo, resumo=ext.resumo, arquivo_original=arq.nome,
        caminho_final=final, caminho_original=arq.caminho, hash_arquivo=hash_arq,
    )
    log.info("%s -> %s (%s)", arq.nome, status, final)
    return {"arquivo": arq.nome, "status": status, "seria": d.status, "pais": d.pais, "cliente": d.cliente,
            "dono": ext.dono_ou_diretor, "servico": d.servico, "tipo": d.tipo, "destino": final,
            "motivo": d.motivo, "resumo": ext.resumo, "confianca": ext.confianca}


def _processar_destino_fixo(arq, cfg, dbx, extrator, registro, ctx, conteudo, hash_arq, hash_curto,
                            destino_fixo, mover) -> dict:
    """Teste com caminho definido pela equipe por CÓDIGOS (ex.: AN1000/AN1000BL/11): o arquivo vai para as
    pastas existentes que começam com esses códigos, com o nome original. Se não achar, vai para revisão. A IA lê o documento só para registrar no Controle o que ela teria sugerido;
    se a IA falhar, o arquivo vai para o destino do mesmo jeito."""
    pasta_real, problema = resolver_destino(dbx.listar_pastas, cfg.pasta_clientes, destino_fixo)
    if pasta_real:
        destino = f"{pasta_real}/{arq.nome}"
    else:
        destino = f"{cfg.pasta_revisar}/[{hash_curto}] {arq.nome}"
    ext, sugestao = None, ""
    try:
        ext = extrator.extrair(arq.nome, conteudo, ctx.sistema, ctx.esquema)
        d = decidir(ext, arq.nome, ctx.catalogo, ctx.regras, cfg.confianca_minima,
                    cfg.pasta_clientes, cfg.pasta_revisar, hash_curto, dbx.listar_pastas)
        sugestao = f"a IA sugeriu: {d.caminho_destino}" + (f" ({d.motivo})" if d.motivo else "")
    except Exception as e:  # noqa: BLE001
        sugestao = f"a IA não conseguiu ler: {type(e).__name__}: {e}"[:1500]
    final = mover(destino)
    status = "SIMULADO" if cfg.simulacao else ("OK" if pasta_real else "PENDENTE")
    if problema:
        sugestao = f"{problema}; {sugestao}"
    registro.inserir(
        status=status, pais=ext.pais if ext else None, cliente=ext.nome_cliente if ext else None,
        dono=ext.dono_ou_diretor if ext else None, servico=ext.servico if ext else None,
        tipo=ext.tipo_documento if ext else None, data_documento=ext.data_documento if ext else None,
        fornecedor=ext.fornecedor_ou_emissor if ext else None, valor_total=ext.valor_total if ext else None,
        moeda=ext.moeda if ext else None, numero_documento=ext.numero_documento if ext else None,
        confianca=ext.confianca if ext else None, motivo=f"destino fixo do teste; {sugestao}",
        resumo=ext.resumo if ext else None, arquivo_original=arq.nome, caminho_final=final,
        caminho_original=arq.caminho, hash_arquivo=hash_arq,
    )
    log.info("%s -> %s (destino fixo)", arq.nome, final)
    return {"arquivo": arq.nome, "status": status, "seria": "OK" if pasta_real else "PENDENTE",
            "destino": final, "motivo": sugestao}


def mover_para_erros(arq, cfg, dbx, registro, motivo):
    try:
        hash_arq = hashlib.sha256(dbx.baixar(arq.caminho)).hexdigest()
    except Exception:  # noqa: BLE001
        hash_arq = arq.content_hash or arq.nome
    destino = f"{cfg.pasta_erros}/[{hash_arq[:8]}] {arq.nome}"
    if cfg.simulacao:
        final = destino
    elif getattr(cfg, "copiar", False):
        final = dbx.copiar(arq.caminho, destino)
    else:
        final = dbx.mover(arq.caminho, destino)
    registro.inserir(
        status="SIMULADO" if cfg.simulacao else "ERRO", arquivo_original=arq.nome,
        caminho_final=final, caminho_original=arq.caminho, hash_arquivo=hash_arq,
        motivo=f"erro técnico: {motivo[:3000]}",
    )


def mostrar_resultados(resultados: list[dict], simulacao: bool) -> None:
    print("\n" + "=" * 70)
    print("SIMULAÇÃO: nada foi movido nem criado no Dropbox." if simulacao else "RESULTADO")
    if not resultados:
        print("Nenhum arquivo processado (pasta vazia ou arquivos ainda sincronizando).")
    rotulos = [("arquivo", "Arquivo"), ("seria", "Decisão"), ("status", "Status"), ("pais", "País"),
               ("cliente", "Cliente"), ("dono", "Dono/Diretor"), ("servico", "Serviço"),
               ("tipo", "Tipo de documento"), ("confianca", "Confiança"), ("resumo", "O que é"),
               ("destino", "Destino"), ("motivo", "Observação")]
    for r in resultados:
        print("-" * 70)
        for chave, rot in rotulos:
            if r.get(chave) not in (None, ""):
                print(f"{rot:>18}: {r[chave]}")
    print("=" * 70 + "\n")


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
    uma_vez = "--uma-vez" in sys.argv
    limite = None
    if "--limite" in sys.argv:
        limite = int(sys.argv[sys.argv.index("--limite") + 1])
    resultados: list[dict] = []
    if not cfg.simulacao:
        for p in (cfg.pasta_revisar, cfg.pasta_erros, cfg.pasta_controle):
            dbx.garantir_pasta_recursiva(p)
        if cfg.destino_fixo:
            try:
                corrigir_pastas_literais(dbx, cfg.pasta_clientes, cfg.destino_fixo)
            except Exception:  # noqa: BLE001
                log.exception("Não consegui corrigir as pastas criadas por engano")

    signal.signal(signal.SIGTERM, _sinal)
    signal.signal(signal.SIGINT, _sinal)
    tentativas: dict[str, int] = {}
    ja_simulados: set[str] = set()     # em simulação o arquivo fica na Entrada: não reprocessar
    ctx: Contexto | None = None
    ultimo_catalogo = 0.0
    ultimo_export = 0.0
    ultimo_backup = ""

    log.info("Iniciado%s. Pasta de entrada: %s. Destino: %s. %s",
             " em MODO SIMULAÇÃO (nada será movido)" if cfg.simulacao
             else (" em MODO CÓPIA (o original fica na pasta de entrada)" if cfg.copiar else ""),
             cfg.pasta_entrada, cfg.pasta_clientes,
             "Uma rodada só." if uma_vez else f"Olhando a cada {cfg.intervalo_segundos}s.")
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

            arquivos = dbx.listar(cfg.pasta_entrada)
            if limite is not None:
                # Testes com limite: começa pelo arquivo mais recente (o que acabou de ser escaneado)
                arquivos.sort(key=lambda a: a.modificado, reverse=True)
            for arq in arquivos:
                if _parar or (limite is not None and len(resultados) >= limite):
                    break
                if not arq.nome.lower().endswith(EXTENSOES):
                    continue
                if arq.modificado < cfg.processar_desde:
                    continue  # chegou antes da ativação: não mexe
                if (cfg.simulacao or cfg.copiar) and arq.content_hash in ja_simulados:
                    continue
                if not dbx.estavel(arq, cfg.segundos_estabilizar):
                    log.info("Ainda sincronizando, fica para a próxima rodada: %s", arq.nome)
                    continue
                # Na rodada única, tenta até 3 vezes seguidas; no modo contínuo, uma vez por ciclo
                for _tentativa in range(MAX_TENTATIVAS if uma_vez else 1):
                    try:
                        r = processar_arquivo(arq, cfg, dbx, extrator, registro, ctx)
                        if r is not None:
                            resultados.append(r)
                            alterou = True
                        tentativas.pop(arq.caminho, None)
                        if cfg.simulacao or cfg.copiar:
                            ja_simulados.add(arq.content_hash)
                        break
                    except Exception as e:  # noqa: BLE001
                        n = tentativas.get(arq.caminho, 0) + 1
                        tentativas[arq.caminho] = n
                        log.exception("Falha (%d/%d) em %s", n, MAX_TENTATIVAS, arq.nome)
                        if n >= MAX_TENTATIVAS:
                            mover_para_erros(arq, cfg, dbx, registro, f"{type(e).__name__}: {e}")
                            resultados.append({"arquivo": arq.nome, "status": "ERRO", "motivo": str(e)})
                            tentativas.pop(arq.caminho, None)
                            if cfg.simulacao or cfg.copiar:
                                ja_simulados.add(arq.content_hash)
                            alterou = True
            # Atualiza o Controle.xlsx no Dropbox (somente leitura: a fonte é o banco).
            # Em simulação nada é gravado no Dropbox.
            if cfg.simulacao:
                pass
            elif alterou or (time.time() - ultimo_export) > 3600:
                dbx.enviar(f"{cfg.pasta_controle}/Controle.xlsx",
                           registro.exportar_excel(ctx.catalogo if ctx else None))
                ultimo_export = time.time()
            # Backup do banco 1x por dia (7 cópias rotativas: uma por dia da semana)
            hoje = datetime.now().strftime("%Y-%m-%d")
            if hoje != ultimo_backup and not cfg.simulacao:
                dia = DIAS_SEMANA[datetime.now().weekday()]
                dbx.enviar(f"{cfg.pasta_controle}/backup/controle-{dia}.db", registro.copia_backup())
                ultimo_backup = hoje
                log.info("Backup diário enviado (%s)", dia)
            ping_ok(cfg.healthcheck_url)
        except Exception as e:  # noqa: BLE001
            log.exception("Erro no ciclo; tentará de novo")
            ping_falha(cfg.healthcheck_url, f"{type(e).__name__}: {e}")
        if uma_vez:
            mostrar_resultados(resultados, cfg.simulacao)
            break
        for _ in range(cfg.intervalo_segundos):
            if _parar:
                break
            time.sleep(1)
    log.info("Encerrado.")


if __name__ == "__main__":
    main()
