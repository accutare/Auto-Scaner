"""Configuração lida de variáveis de ambiente (arquivo .env)."""
import os
from dataclasses import dataclass


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Variável de ambiente obrigatória não definida: {name}")
    return value or ""


@dataclass(frozen=True)
class Config:
    # Dropbox (use refresh token: não expira, ao contrário do token curto)
    dropbox_app_key: str
    dropbox_app_secret: str
    dropbox_refresh_token: str

    # Pastas dentro do Dropbox
    pasta_entrada: str
    pasta_processados: str
    pasta_revisar: str
    pasta_erros: str
    pasta_controle: str

    # IA
    anthropic_api_key: str
    claude_model: str

    # Regras
    confianca_minima: float
    intervalo_segundos: int
    segundos_estabilizar: int

    # Armazenamento local (monte como volume no servidor para não perder)
    dados_dir: str
    clientes_csv: str

    # Monitoramento (opcional): URL de "ping" do healthchecks.io ou similar
    healthcheck_url: str


def carregar() -> Config:
    return Config(
        dropbox_app_key=_get("DROPBOX_APP_KEY", required=True),
        dropbox_app_secret=_get("DROPBOX_APP_SECRET", required=True),
        dropbox_refresh_token=_get("DROPBOX_REFRESH_TOKEN", required=True),
        pasta_entrada=_get("PASTA_ENTRADA", "/Entrada"),
        pasta_processados=_get("PASTA_PROCESSADOS", "/Processados"),
        pasta_revisar=_get("PASTA_REVISAR", "/Revisar"),
        pasta_erros=_get("PASTA_ERROS", "/Erros"),
        pasta_controle=_get("PASTA_CONTROLE", "/Controle"),
        anthropic_api_key=_get("ANTHROPIC_API_KEY", required=True),
        claude_model=_get("CLAUDE_MODEL", "claude-sonnet-5-5"),
        confianca_minima=float(_get("CONFIANCA_MINIMA", "0.85")),
        intervalo_segundos=int(_get("INTERVALO_SEGUNDOS", "60")),
        segundos_estabilizar=int(_get("SEGUNDOS_ESTABILIZAR", "30")),
        dados_dir=_get("DADOS_DIR", "/data"),
        clientes_csv=_get("CLIENTES_CSV", "/data/clientes.csv"),
        healthcheck_url=_get("HEALTHCHECK_URL", "").rstrip("/"),
    )
