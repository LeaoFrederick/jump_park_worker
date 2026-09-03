"""
src/bot/notifications.py
Gerencia o envio de notificações formatadas (Embeds) para o Discord via Webhook e/ou Bot.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

import requests

from src.config import (
    DISCORD_NOTIFICATION_CHANNEL_ID,
    DISCORD_WEBHOOK_URL,
)

log = logging.getLogger(__name__)




def _build_auto_unlock_embed_dict(
    placa: str,
    valor_taxa: float,
    estabelecimento_origem: str,
    estabelecimentos_afetados: list[str] | str,
    os_id: str | None = None,
    exit_datetime: str | None = None,
    evento_id: int | None = None,
) -> dict:
    """Gera o dicionário de Embed compatível com a API de Webhook do Discord."""
    if isinstance(estabelecimentos_afetados, list):
        afetados_str = ", ".join(estabelecimentos_afetados)
    else:
        afetados_str = str(estabelecimentos_afetados)

    fields = [
        {"name": "🚗 Placa", "value": f"`{placa}`", "inline": True},
        {"name": "💰 Taxa Paga", "value": f"`R$ {valor_taxa:.2f}`", "inline": True},
        {"name": "🏢 Unidade Pagadora", "value": f"**{estabelecimento_origem}**", "inline": True},
        {"name": "🔓 Unidades Liberadas", "value": afetados_str or "Todas", "inline": False},
        {"name": "🧾 Ordem de Serviço (OS)", "value": f"`#{os_id}`" if os_id else "_Não informada_", "inline": True},
        {"name": "🕒 Saída/Pagamento OS", "value": f"`{exit_datetime}`" if exit_datetime else "_Não informada_", "inline": True},
    ]
    if evento_id:
        fields.append({"name": "📋 Registro (BD)", "value": f"`#{evento_id}`", "inline": True})

    return {
        "title": "🔓 Desbloqueio Automático Realizado!",
        "description": "Veículo liberado do bloqueio após detecção de pagamento da taxa.",
        "color": 0x2ECC71,  # Verde
        "fields": fields,
        "footer": {"text": "Jump Park Worker • Monitoramento Automático"},
        "timestamp": datetime.utcnow().isoformat(),
    }


def notificar_desbloqueio_webhook(
    placa: str,
    valor_taxa: float,
    estabelecimento_origem: str,
    estabelecimentos_afetados: list[str] | str,
    os_id: str | None = None,
    exit_datetime: str | None = None,
    evento_id: int | None = None,
) -> None:
    """Envia notificação de desbloqueio automático via Webhook do Discord."""
    if not DISCORD_WEBHOOK_URL:
        return

    embed = _build_auto_unlock_embed_dict(
        placa=placa,
        valor_taxa=valor_taxa,
        estabelecimento_origem=estabelecimento_origem,
        estabelecimentos_afetados=estabelecimentos_afetados,
        os_id=os_id,
        exit_datetime=exit_datetime,
        evento_id=evento_id,
    )

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=5,
        )
        log.info("[DISCORD] Notificação de desbloqueio da placa %s enviada via Webhook.", placa)
    except Exception as exc:
        log.warning("[DISCORD WEBHOOK] Falha ao enviar notificação: %s", exc)





def notificar_desbloqueio(
    placa: str,
    valor_taxa: float,
    estabelecimento_origem: str,
    estabelecimentos_afetados: list[str] | str,
    os_id: str | None = None,
    exit_datetime: str | None = None,
    evento_id: int | None = None,
) -> None:
    """
    Função principal de notificação de desbloqueio automático.
    Dispara apenas para o Webhook.
    """
    notificar_desbloqueio_webhook(
        placa=placa,
        valor_taxa=valor_taxa,
        estabelecimento_origem=estabelecimento_origem,
        estabelecimentos_afetados=estabelecimentos_afetados,
        os_id=os_id,
        exit_datetime=exit_datetime,
        evento_id=evento_id,
    )
