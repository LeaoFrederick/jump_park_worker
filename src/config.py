"""
src/config.py
Configurações globais, carregamento de variáveis de ambiente e modelos de estabelecimento.
"""

import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime
import requests
from dotenv import load_dotenv

# Carrega o arquivo .env
load_dotenv()

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Patch IPv4-only
# ──────────────────────────────────────────────────────────────────────────────
def _apply_ipv4_patch() -> None:
    _original_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo_ipv4_only(*args, **kwargs):
        return [
            addr
            for addr in _original_getaddrinfo(*args, **kwargs)
            if addr[0] == socket.AF_INET
        ]

    socket.getaddrinfo = _getaddrinfo_ipv4_only
    log.debug("Patch IPv4-only aplicado.")


_apply_ipv4_patch()


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de configuração por estabelecimento
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class EstablishmentConfig:
    """Agrupa todas as credenciais e metadados de um estabelecimento Jump Park."""
    label: str                    # Nome legível (ex: "COBRANÇA", "CANAL", "PRINCIPAL")
    prefix: str                   # Prefixo no .env (ex: "COBRANCA", "CANAL", "PRINCIPAL")
    integration_id: str           # ID de integração na API
    establishment_id: str         # ID do estabelecimento na API
    token: str                    # Bearer token de acesso
    origin: str = ""              # Domínio cadastrado no Site Admin (opcional)
    blocked_client_id: str = ""   # ID do cliente "CARRO BLOQUEADO" (pode estar vazio)

    @property
    def is_ready(self) -> bool:
        """Retorna True se o estabelecimento tem todas as credenciais necessárias."""
        return bool(
            self.integration_id
            and self.establishment_id
            and self.token
            and self.blocked_client_id
        )


_ESTABLISHMENT_PREFIXES = [
    ("COBRANCA", "COBRANÇA"),
    ("CANAL",    "CANAL"),
    ("PRINCIPAL","PRINCIPAL"),
]


def load_establishments() -> list[EstablishmentConfig]:
    """Carrega todos os estabelecimentos configurados no .env."""
    establishments = []
    for prefix, label in _ESTABLISHMENT_PREFIXES:
        integration_id    = os.getenv(f"{prefix}_INTEGRATION_ID", "")
        establishment_id  = os.getenv(f"{prefix}_ESTABLISHMENT_ID", "")
        token             = os.getenv(f"{prefix}_ACCESS_TOKEN", "")
        origin            = os.getenv(f"{prefix}_ORIGIN", "").strip()
        blocked_client_id = os.getenv(f"{prefix}_BLOCKED_CLIENT_ID", "")

        if not integration_id:
            log.debug("[CONFIG] Prefixo %s sem INTEGRATION_ID — ignorado.", prefix)
            continue

        config = EstablishmentConfig(
            label=label,
            prefix=prefix,
            integration_id=integration_id,
            establishment_id=establishment_id,
            token=token,
            origin=origin,
            blocked_client_id=blocked_client_id,
        )

        if config.is_ready:
            log.info(
                "[CONFIG] ✔ %s (ID %s) — pronto para monitoramento.",
                label, establishment_id,
            )
        else:
            missing = []
            if not establishment_id: missing.append("ESTABLISHMENT_ID")
            if not token:            missing.append("ACCESS_TOKEN")
            if not blocked_client_id: missing.append("BLOCKED_CLIENT_ID")
            log.warning(
                "[CONFIG] ⚠ %s (ID %s) — pendente, faltam: %s. "
                "Será ignorado no monitoramento até ser configurado.",
                label, establishment_id or "?", ", ".join(missing),
            )

        establishments.append(config)

    return establishments


def get_ready_establishments() -> list[EstablishmentConfig]:
    """Retorna os estabelecimentos prontos (is_ready == True)."""
    establishments = load_establishments()
    return [e for e in establishments if e.is_ready]


def validate_env(establishments: list[EstablishmentConfig]) -> None:
    """Valida que pelo menos um estabelecimento está completamente configurado."""
    if not establishments:
        raise EnvironmentError(
            "Nenhum estabelecimento configurado no .env.\n"
            "Defina ao menos um bloco com prefixo (ex: COBRANCA_INTEGRATION_ID, "
            "COBRANCA_ESTABLISHMENT_ID, COBRANCA_ACCESS_TOKEN, COBRANCA_BLOCKED_CLIENT_ID)."
        )

    ready = [e for e in establishments if e.is_ready]
    if not ready:
        details = []
        for e in establishments:
            missing = []
            if not e.integration_id:    missing.append(f"{e.prefix}_INTEGRATION_ID")
            if not e.establishment_id:  missing.append(f"{e.prefix}_ESTABLISHMENT_ID")
            if not e.token:             missing.append(f"{e.prefix}_ACCESS_TOKEN")
            if not e.blocked_client_id: missing.append(f"{e.prefix}_BLOCKED_CLIENT_ID")
            details.append(f"  {e.label}: faltam {missing}")
        raise EnvironmentError(
            "Nenhum estabelecimento está completamente configurado:\n"
            + "\n".join(details)
            + "\nPreencha pelo menos um bloco completo no .env."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Configurações globais
# ──────────────────────────────────────────────────────────────────────────────
BASE_URL          = "https://new-web.jumpparkapi.com.br"

POLLING_INTERVAL  = int(os.getenv("POLLING_INTERVAL_SECONDS", "10"))   # segundos entre ciclos
CACHE_DURATION    = int(os.getenv("CACHE_DURATION_SECONDS", "1800"))    # 30 min
TAXA_VALOR        = float(os.getenv("TAXA_BLOQUEIO_VALOR", "200.00"))   # valor da taxa de desbloqueio
WINDOW_DAYS       = int(os.getenv("SEARCH_WINDOW_DAYS", "1"))           # janela de busca de OS

API_HOST          = os.getenv("API_HOST", "0.0.0.0")
API_PORT          = int(os.getenv("API_PORT", "8000"))

DATABASE_URL      = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_NOTIFICATION_CHANNEL_ID = os.getenv(
    "DISCORD_NOTIFICATION_CHANNEL_ID",
    os.getenv("DISCORD_CHANNEL_ID", "")
).strip()


def build_headers(config: EstablishmentConfig) -> dict:
    """Constrói os headers HTTP obrigatórios para a Jump Park API."""
    headers = {
        "Authorization": f"Bearer {config.token}",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    if config.origin:
        headers["Origin"] = config.origin
    return headers


def alertar_discord(mensagem: str) -> None:
    """
    Envia uma mensagem de alerta simples para o canal Discord configurado via webhook.
    Falhas de rede são engolidas para nunca travar o worker.
    """
    if not DISCORD_WEBHOOK_URL:
        return

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": mensagem},
            timeout=5,
        )
    except Exception:
        pass


def notificar_desbloqueio_discord_webhook(
    placa: str,
    valor_taxa: float,
    estabelecimento_origem: str,
    estabelecimentos_afetados: list[str] | str,
    os_id: str | None = None,
    exit_datetime: str | None = None,
    evento_id: int | None = None,
) -> None:
    """
    Envia um Embed visual e formatado para o webhook do Discord informando
    o desbloqueio automático do veículo.
    """
    if not DISCORD_WEBHOOK_URL:
        return

    if isinstance(estabelecimentos_afetados, list):
        afetados_str = ", ".join(estabelecimentos_afetados)
    else:
        afetados_str = str(estabelecimentos_afetados)

    embed = {
        "title": "🔓 Desbloqueio Automático Realizado!",
        "description": "Veículo liberado do bloqueio após detecção de pagamento da taxa.",
        "color": 0x2ECC71,  # Verde
        "fields": [
            {"name": "🚗 Placa", "value": f"`{placa}`", "inline": True},
            {"name": "💰 Taxa Paga", "value": f"`R$ {valor_taxa:.2f}`", "inline": True},
            {"name": "🏢 Unidade Pagadora", "value": f"**{estabelecimento_origem}**", "inline": True},
            {"name": "🔓 Unidades Liberadas", "value": afetados_str or "Todas", "inline": False},
            {"name": "🧾 Ordem de Serviço (OS)", "value": f"`#{os_id}`" if os_id else "_Não informada_", "inline": True},
            {"name": "🕒 Saída/Pagamento OS", "value": f"`{exit_datetime}`" if exit_datetime else "_Não informada_", "inline": True},
            {"name": "📋 Registro (BD)", "value": f"`#{evento_id}`" if evento_id else "_N/A_", "inline": True},
        ],
        "footer": {"text": "Jump Park Worker • Monitoramento Automático"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=5,
        )
    except Exception as exc:
        log.warning("[DISCORD WEBHOOK] Falha ao enviar notificação de desbloqueio: %s", exc)

