"""Configuração lida de variáveis de ambiente (arquivo .env)."""
import os
from dataclasses import dataclass


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Variável de ambiente obrigatória não definida: {name}")
    return value or ""


def _bool(name: str, default: str = "false") -> bool:
    return _get(name, default).strip().lower() in ("1", "true", "sim", "yes", "on")


@dataclass(frozen=True)
class Config:
    # Dropbox (use refresh token: não expira, ao contrário do token curto)
    dropbox_app_key: str
    dropbox_app_secret: str
    dropbox_refresh_token: str
    dropbox_team_space: str     # auto | off

    # Pastas dentro do Dropbox
    pasta_client_data: str      # raiz dos clientes (pastas de serviço -> pastas de cliente)
    pasta_entrada: str          # onde o scanner salva
    pasta_revisar: str
    pasta_erros: str
    pasta_controle: str

    # IA
    anthropic_api_key: str
    claude_model: str
    claude_esforco: str

    # Regras
    confianca_minima: float
    intervalo_segundos: int
    segundos_estabilizar: int
    catalogo_minutos: int
    simulacao: bool             # true: só registra a decisão no Controle.xlsx, não move nada

    # Armazenamento local (monte como volume no servidor para não perder)
    dados_dir: str
    servicos_json: str
    clientes_extra_csv: str

    # Monitoramento (opcional): URL de "ping" do healthchecks.io ou similar
    healthcheck_url: str


def carregar() -> Config:
    dados = _get("DADOS_DIR", "/data")
    return Config(
        dropbox_app_key=_get("DROPBOX_APP_KEY", required=True),
        dropbox_app_secret=_get("DROPBOX_APP_SECRET", required=True),
        dropbox_refresh_token=_get("DROPBOX_REFRESH_TOKEN", required=True),
        dropbox_team_space=_get("DROPBOX_TEAM_SPACE", "auto").strip().lower(),
        pasta_client_data=_get(
            "PASTA_CLIENT_DATA", "/!Accurate Complete Accounting Ltd - data files/Client Data"
        ).rstrip("/"),
        pasta_entrada=_get("PASTA_ENTRADA", "/Docflow/Entrada").rstrip("/"),
        pasta_revisar=_get("PASTA_REVISAR", "/Docflow/Revisar").rstrip("/"),
        pasta_erros=_get("PASTA_ERROS", "/Docflow/Erros").rstrip("/"),
        pasta_controle=_get("PASTA_CONTROLE", "/Docflow/Controle").rstrip("/"),
        anthropic_api_key=_get("ANTHROPIC_API_KEY", required=True),
        claude_model=_get("CLAUDE_MODEL", "claude-sonnet-5-5"),
        claude_esforco=_get("CLAUDE_ESFORCO", "medium"),
        confianca_minima=float(_get("CONFIANCA_MINIMA", "0.85")),
        intervalo_segundos=int(_get("INTERVALO_SEGUNDOS", "60")),
        segundos_estabilizar=int(_get("SEGUNDOS_ESTABILIZAR", "30")),
        catalogo_minutos=int(_get("CATALOGO_MINUTOS", "15")),
        simulacao=_bool("SIMULACAO", "true"),
        dados_dir=dados,
        servicos_json=_get("SERVICOS_JSON", os.path.join(dados, "servicos.json")),
        clientes_extra_csv=_get("CLIENTES_EXTRA_CSV", os.path.join(dados, "clientes_extra.csv")),
        healthcheck_url=_get("HEALTHCHECK_URL", "").rstrip("/"),
    )
