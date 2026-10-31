"""Monitoramento: avisa um serviço externo (ex.: healthchecks.io) que o programa está vivo.

Se o programa parar (servidor caiu, travou, foi desligado), os "pings" deixam de chegar e o
serviço envia um e-mail de alerta. Isso é o único jeito confiável de saber de uma queda, porque
um programa parado não consegue avisar que parou.
"""
import logging
import urllib.request

log = logging.getLogger("docflow.monitor")


def _chamar(url: str, corpo: bytes | None = None) -> None:
    try:
        req = urllib.request.Request(url, data=corpo, method="POST" if corpo is not None else "GET")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # noqa: BLE001  (monitoramento nunca pode derrubar o programa)
        log.warning("Não consegui avisar o monitoramento: %s", e)


def ping_ok(base_url: str) -> None:
    if base_url:
        _chamar(base_url)


def ping_falha(base_url: str, mensagem: str = "") -> None:
    """Sinaliza falha imediata (o serviço envia o alerta sem esperar o prazo)."""
    if base_url:
        _chamar(base_url + "/fail", mensagem[:500].encode("utf-8", "ignore"))
