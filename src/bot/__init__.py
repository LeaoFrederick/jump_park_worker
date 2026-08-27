"""
src/bot package.
Exporta a função para inicializar o bot Discord.
"""

from src.bot.notifications import notificar_desbloqueio
from src.bot.discord_bot import start_discord_bot

__all__ = ["notificar_desbloqueio", "start_discord_bot"]
