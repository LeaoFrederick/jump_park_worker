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

# Referência global opcional para a instância do bot (injetada por discord_bot.py quando ativo)
_bot_instance: Any = None


def register_bot_instance(bot: Any) -> None:
    """Registra a instância do bot para envio de mensagens em canais."""
    global _bot_instance
    _bot_instance = bot


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


def notificar_desbloqueio_bot_channel(
    placa: str,
    valor_taxa: float,
    estabelecimento_origem: str,
    estabelecimentos_afetados: list[str] | str,
    os_id: str | None = None,
    exit_datetime: str | None = None,
    evento_id: int | None = None,
) -> None:
    """Envia notificação de desbloqueio automático para o canal de texto do Discord Bot."""
    if not _bot_instance or not DISCORD_NOTIFICATION_CHANNEL_ID:
        return

    try:
        channel_id = int(DISCORD_NOTIFICATION_CHANNEL_ID)
    except ValueError:
        log.warning(
            "[DISCORD BOT] DISCORD_NOTIFICATION_CHANNEL_ID inválido (deve ser numérico): %s",
            DISCORD_NOTIFICATION_CHANNEL_ID,
        )
        return

    if not (_bot_instance.is_ready() and _bot_instance.loop and _bot_instance.loop.is_running()):
        return

    try:
        import discord

        if isinstance(estabelecimentos_afetados, list):
            afetados_str = ", ".join(estabelecimentos_afetados)
        else:
            afetados_str = str(estabelecimentos_afetados)

        embed = discord.Embed(
            title="🔓 Desbloqueio Automático Realizado!",
            description="Veículo liberado do bloqueio após detecção de pagamento da taxa.",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="🚗 Placa", value=f"`{placa}`", inline=True)
        embed.add_field(name="💰 Taxa Paga", value=f"`R$ {valor_taxa:.2f}`", inline=True)
        embed.add_field(name="🏢 Unidade Pagadora", value=f"**{estabelecimento_origem}**", inline=True)
        embed.add_field(name="🔓 Unidades Liberadas", value=afetados_str or "Todas", inline=False)
        embed.add_field(name="🧾 Ordem de Serviço (OS)", value=f"`#{os_id}`" if os_id else "_Não informada_", inline=True)
        embed.add_field(name="🕒 Saída/Pagamento OS", value=f"`{exit_datetime}`" if exit_datetime else "_Não informada_", inline=True)
        if evento_id:
            embed.add_field(name="📋 Registro (BD)", value=f"`#{evento_id}`", inline=True)

        embed.set_footer(text="Jump Park Worker • Monitoramento Automático")

        async def _send() -> None:
            try:
                ch = _bot_instance.get_channel(channel_id)
                if ch is None:
                    ch = await _bot_instance.fetch_channel(channel_id)
                if ch and hasattr(ch, "send"):
                    await ch.send(embed=embed)
                    log.info("[DISCORD BOT] Notificação enviada ao canal %s com sucesso.", channel_id)
            except Exception as e:
                log.warning("[DISCORD BOT] Falha ao despachar mensagem no canal %s: %s", channel_id, e)

        asyncio.run_coroutine_threadsafe(_send(), _bot_instance.loop)
    except Exception as exc:
        log.warning("[DISCORD BOT] Erro ao preparar notificação para canal do bot: %s", exc)


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
    Dispara tanto para o Webhook quanto para o Canal do Bot (se configurados).
    """
    # 1. Notifica via Webhook
    notificar_desbloqueio_webhook(
        placa=placa,
        valor_taxa=valor_taxa,
        estabelecimento_origem=estabelecimento_origem,
        estabelecimentos_afetados=estabelecimentos_afetados,
        os_id=os_id,
        exit_datetime=exit_datetime,
        evento_id=evento_id,
    )

    # 2. Notifica via Canal do Bot (se ativo)
    notificar_desbloqueio_bot_channel(
        placa=placa,
        valor_taxa=valor_taxa,
        estabelecimento_origem=estabelecimento_origem,
        estabelecimentos_afetados=estabelecimentos_afetados,
        os_id=os_id,
        exit_datetime=exit_datetime,
        evento_id=evento_id,
    )
