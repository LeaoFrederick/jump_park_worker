"""
jump_park_worker/discord_bot.py
Bot do Discord interativo para monitoramento e controle remoto do Jump Park Worker.

Comandos disponíveis (via Slash / e Prefixo !):
  - /status ou !status          — Exibe a saúde do worker, estabelecimentos e métricas
  - /placas ou !placas          — Lista placas atualmente bloqueadas por estabelecimento
  - /bloquear <placa> [motivo]   — Bloqueia veículo manualmente em todas as unidades
  - /desbloquear <placa> [motivo]— Desbloqueia veículo em todas as unidades
  - /eventos [limite]            — Consulta os últimos eventos registrados no MySQL
  - /ajuda ou !ajuda            — Guia de uso dos comandos

Roda em thread daemon independente com asyncio event loop próprio.
"""

import asyncio
import logging
import os
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# Configuração de intents
intents = discord.Intents.default()
intents.message_content = True  # Permite responder a mensagens de texto com prefixo !

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ──────────────────────────────────────────────────────────────────────────────
# Eventos do Bot
# ──────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    log.info("[DISCORD BOT] Conectado como %s (ID: %s)", bot.user, bot.user.id)
    try:
        synced = await bot.tree.sync()
        log.info("[DISCORD BOT] %d slash commands sincronizados com sucesso.", len(synced))
    except Exception as e:
        log.warning("[DISCORD BOT] Falha ao sincronizar slash commands: %s", e)

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Jump Park Worker | /status",
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# Funções Auxiliares
# ──────────────────────────────────────────────────────────────────────────────

def _get_worker_status_embed() -> discord.Embed:
    from main import (
        CACHE_DURATION,
        POLLING_INTERVAL,
        TAXA_VALOR,
        WINDOW_DAYS,
        get_blocked_plates,
        load_establishments,
    )

    establishments = load_establishments()
    ready = [e for e in establishments if e.is_ready]

    embed = discord.Embed(
        title="🟢 Status do Jump Park Worker",
        description="Monitoramento contínuo e API em execução na Oracle VM.",
        color=discord.Color.green(),
        timestamp=datetime.utcnow(),
    )

    embed.add_field(
        name="⚙️ Configurações",
        value=(
            f"• **Polling:** `{POLLING_INTERVAL}s`\n"
            f"• **Cache:** `{CACHE_DURATION}s`\n"
            f"• **Janela OS:** `{WINDOW_DAYS} dia(s)`\n"
            f"• **Taxa Desbloqueio:** `R$ {TAXA_VALOR:.2f}`"
        ),
        inline=False,
    )

    estab_text = []
    for cfg in ready:
        try:
            plates = get_blocked_plates(cfg)
            qtd = len(plates)
        except Exception:
            qtd = "Erro"
        estab_text.append(f"• **{cfg.label}** (ID `{cfg.establishment_id}`): `{qtd}` placa(s) bloqueada(s)")

    embed.add_field(
        name=f"🏢 Estabelecimentos Ativos ({len(ready)})",
        value="\n".join(estab_text) if estab_text else "Nenhum estabelecimento ativo.",
        inline=False,
    )

    embed.set_footer(text="Jump Park Worker • Oracle Cloud VM")
    return embed


def _get_plates_embed() -> discord.Embed:
    from main import get_blocked_plates, load_establishments

    establishments = load_establishments()
    ready = [e for e in establishments if e.is_ready]

    embed = discord.Embed(
        title="🚗 Placas Atualmente Bloqueadas",
        description="Veículos vinculados à conta 'CARRO BLOQUEADO' no Jump Park.",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow(),
    )

    for cfg in ready:
        try:
            plates = get_blocked_plates(cfg)
            if plates:
                lista_str = ", ".join(f"`{p}`" for p in plates)
                if len(lista_str) > 1000:
                    lista_str = lista_str[:990] + "... (lista truncada)"
                embed.add_field(
                    name=f"📍 {cfg.label} ({len(plates)})",
                    value=lista_str,
                    inline=False,
                )
            else:
                embed.add_field(
                    name=f"📍 {cfg.label} (0)",
                    value="_Nenhuma placa bloqueada no momento._",
                    inline=False,
                )
        except Exception as exc:
            embed.add_field(
                name=f"📍 {cfg.label}",
                value=f"⚠️ Erro ao consultar: `{exc}`",
                inline=False,
            )

    embed.set_footer(text="Jump Park Worker")
    return embed


def _exec_bloqueio(plate: str, reason: str, author_name: str) -> discord.Embed:
    from api import _block_vehicle_on_establishment, _get_ready_establishments
    from database import registrar_evento

    plate = plate.upper().strip()
    if len(plate) < 7:
        return discord.Embed(
            title="❌ Placa Inválida",
            description=f"A placa `{plate}` não possui o formato mínimo de 7 caracteres.",
            color=discord.Color.red(),
        )

    try:
        ready = _get_ready_establishments()
    except Exception as e:
        return discord.Embed(
            title="❌ Erro de Configuração",
            description=str(e),
            color=discord.Color.red(),
        )

    sucesso_em = []
    ja_bloqueada_em = []
    falha_em = []

    for cfg in ready:
        res = _block_vehicle_on_establishment(cfg, plate)
        if res["status"] == "ok":
            sucesso_em.append(cfg.label)
        elif res["status"] == "already_blocked":
            ja_bloqueada_em.append(cfg.label)
        else:
            falha_em.append(f"{cfg.label}: {res.get('detail', 'Erro')}")

    evento_id = None
    if sucesso_em:
        evento_id = registrar_evento(
            evento="BLOQUEIO",
            metodo="MANUAL",
            autor=f"{author_name} (Discord)",
            motivo=reason or "Bloqueio via Discord Bot",
            placa=plate,
            cliente_id=ready[0].blocked_client_id,
            estabelecimento_origem="DISCORD",
            estabelecimentos_afetados=", ".join(sucesso_em),
        )

    if len(sucesso_em) == len(ready):
        color = discord.Color.green()
        title = f"🔒 Placa {plate} Bloqueada com Sucesso!"
        desc = f"Veículo adicionado ao CARRO BLOQUEADO em **todas** as {len(ready)} unidades."
    elif len(ja_bloqueada_em) == len(ready):
        color = discord.Color.gold()
        title = f"⚠️ Placa {plate} Já Estava Bloqueada"
        desc = "Esta placa já constava na lista de bloqueios de todos os estabelecimentos."
    elif sucesso_em:
        color = discord.Color.orange()
        title = f"ℹ️ Placa {plate} Processada Parcialmente"
        desc = "Ação realizada com variações por estabelecimento."
    else:
        color = discord.Color.red()
        title = f"❌ Falha ao Bloquear {plate}"
        desc = "Não foi possível concluir o bloqueio."

    embed = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.utcnow())
    embed.add_field(name="Placa", value=f"`{plate}`", inline=True)
    embed.add_field(name="Operador", value=author_name, inline=True)
    embed.add_field(name="Motivo", value=reason or "_Não informado_", inline=False)

    if sucesso_em:
        embed.add_field(name="✅ Bloqueada em", value=", ".join(sucesso_em), inline=False)
    if ja_bloqueada_em:
        embed.add_field(name="⚠️ Já Bloqueada em", value=", ".join(ja_bloqueada_em), inline=False)
    if falha_em:
        embed.add_field(name="❌ Erros", value="\n".join(falha_em), inline=False)
    if evento_id:
        embed.add_field(name="ID do Evento (BD)", value=f"`#{evento_id}`", inline=True)

    return embed


def _exec_desbloqueio(plate: str, reason: str, author_name: str) -> discord.Embed:
    from api import _get_ready_establishments, _unblock_vehicle_on_establishment
    from database import registrar_evento

    plate = plate.upper().strip()
    if len(plate) < 7:
        return discord.Embed(
            title="❌ Placa Inválida",
            description=f"A placa `{plate}` não possui o formato mínimo de 7 caracteres.",
            color=discord.Color.red(),
        )

    try:
        ready = _get_ready_establishments()
    except Exception as e:
        return discord.Embed(
            title="❌ Erro de Configuração",
            description=str(e),
            color=discord.Color.red(),
        )

    sucesso_em = []
    nao_encontrada_em = []
    falha_em = []

    for cfg in ready:
        res = _unblock_vehicle_on_establishment(cfg, plate)
        if res["status"] == "ok":
            sucesso_em.append(cfg.label)
        elif res["status"] == "not_found":
            nao_encontrada_em.append(cfg.label)
        else:
            falha_em.append(f"{cfg.label}: {res.get('detail', 'Erro')}")

    evento_id = None
    if sucesso_em:
        evento_id = registrar_evento(
            evento="DESBLOQUEIO",
            metodo="MANUAL",
            autor=f"{author_name} (Discord)",
            motivo=reason or "Desbloqueio via Discord Bot",
            placa=plate,
            cliente_id=ready[0].blocked_client_id,
            estabelecimento_origem="DISCORD",
            estabelecimentos_afetados=", ".join(sucesso_em),
        )

    if len(sucesso_em) == len(ready):
        color = discord.Color.green()
        title = f"🔓 Placa {plate} Desbloqueada com Sucesso!"
        desc = f"Veículo liberado do CARRO BLOQUEADO em **todas** as {len(ready)} unidades."
    elif len(nao_encontrada_em) == len(ready):
        color = discord.Color.gold()
        title = f"ℹ️ Placa {plate} Não Encontrada"
        desc = "A placa informada não constava na lista de bloqueio de nenhum estabelecimento."
    elif sucesso_em:
        color = discord.Color.orange()
        title = f"ℹ️ Placa {plate} Desbloqueada Parcialmente"
        desc = "Ação realizada com variações por estabelecimento."
    else:
        color = discord.Color.red()
        title = f"❌ Falha ao Desbloquear {plate}"
        desc = "Não foi possível concluir o desbloqueio."

    embed = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.utcnow())
    embed.add_field(name="Placa", value=f"`{plate}`", inline=True)
    embed.add_field(name="Operador", value=author_name, inline=True)
    embed.add_field(name="Motivo", value=reason or "_Não informado_", inline=False)

    if sucesso_em:
        embed.add_field(name="✅ Desbloqueada em", value=", ".join(sucesso_em), inline=False)
    if nao_encontrada_em:
        embed.add_field(name="ℹ️ Não constava em", value=", ".join(nao_encontrada_em), inline=False)
    if falha_em:
        embed.add_field(name="❌ Erros", value="\n".join(falha_em), inline=False)
    if evento_id:
        embed.add_field(name="ID do Evento (BD)", value=f"`#{evento_id}`", inline=True)

    return embed


def _get_recent_events_embed(limit: int = 5) -> discord.Embed:
    from database import Evento, get_session

    limit = min(max(1, limit), 15)

    embed = discord.Embed(
        title=f"📋 Últimos {limit} Eventos Registrados",
        color=discord.Color.dark_teal(),
        timestamp=datetime.utcnow(),
    )

    try:
        with get_session() as session:
            eventos = (
                session.query(Evento)
                .order_by(Evento.timestamp.desc())
                .limit(limit)
                .all()
            )

            if not eventos:
                embed.description = "Nenhum evento registrado no banco de dados ainda."
                return embed

            for ev in eventos:
                dt_str = ev.timestamp.strftime("%d/%m %H:%M:%S") if ev.timestamp else "N/A"
                icon = "🔒" if ev.evento == "BLOQUEIO" else "🔓"
                titulo = f"{icon} #{ev.id} • {ev.evento} [{ev.metodo}] — {ev.placa}"
                valor = (
                    f"**Data:** `{dt_str}` | **Autor:** {ev.autor}\n"
                    f"**Origem:** `{ev.estabelecimento_origem}` | **Afetados:** `{ev.estabelecimentos_afetados or 'N/A'}`\n"
                    f"**Motivo:** {ev.motivo or '_Sem motivo_'}"
                )
                if ev.valor_taxa:
                    valor += f"\n**Taxa Paga:** `R$ {ev.valor_taxa:.2f}` (OS: `{ev.os_id or 'N/A'}`)"

                embed.add_field(name=titulo, value=valor, inline=False)

    except Exception as exc:
        embed.description = f"⚠️ Erro ao consultar banco de dados: `{exc}`"
        embed.color = discord.Color.red()

    embed.set_footer(text="MySQL Database • Jump Park Worker")
    return embed


def _get_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📖 Central de Comandos — Jump Park Worker",
        description="Você pode executar os comandos usando **Slash (/)** ou **Prefixo (!)**:",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="📊 `/status` ou `!status`",
        value="Verifica a saúde do worker, configurações e quantidade de placas bloqueadas.",
        inline=False,
    )
    embed.add_field(
        name="🚗 `/placas` ou `!placas`",
        value="Exibe a lista de todas as placas bloqueadas por unidade.",
        inline=False,
    )
    embed.add_field(
        name="🔒 `/bloquear <placa> [motivo]` ou `!bloquear <placa>`",
        value="Adiciona a placa ao cliente 'CARRO BLOQUEADO' em todas as unidades ativas.",
        inline=False,
    )
    embed.add_field(
        name="🔓 `/desbloquear <placa> [motivo]` ou `!desbloquear <placa>`",
        value="Remove a placa do bloqueio em todas as unidades imediatamente.",
        inline=False,
    )
    embed.add_field(
        name="📋 `/eventos [limite]` ou `!eventos [limite]`",
        value="Lista os últimos eventos de bloqueio/desbloqueio persistidos no MySQL.",
        inline=False,
    )
    embed.set_footer(text="Jump Park Worker Discord Bot")
    return embed


# ──────────────────────────────────────────────────────────────────────────────
# Slash Commands (Discord App Commands)
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="status", description="Exibe o status atual do Jump Park Worker")
async def slash_status(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await asyncio.to_thread(_get_worker_status_embed)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="placas", description="Lista todas as placas bloqueadas por unidade")
async def slash_placas(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await asyncio.to_thread(_get_plates_embed)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="bloquear", description="Bloqueia um veículo em todos os estabelecimentos")
@app_commands.describe(placa="Placa do veículo (ex: ABC1234)", motivo="Motivo do bloqueio")
async def slash_bloquear(interaction: discord.Interaction, placa: str, motivo: str = ""):
    await interaction.response.defer()
    autor = interaction.user.display_name
    embed = await asyncio.to_thread(_exec_bloqueio, placa, motivo, autor)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="desbloquear", description="Desbloqueia um veículo em todos os estabelecimentos")
@app_commands.describe(placa="Placa do veículo (ex: ABC1234)", motivo="Motivo do desbloqueio")
async def slash_desbloquear(interaction: discord.Interaction, placa: str, motivo: str = ""):
    await interaction.response.defer()
    autor = interaction.user.display_name
    embed = await asyncio.to_thread(_exec_desbloqueio, placa, motivo, autor)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="eventos", description="Consulta os últimos eventos salvos no MySQL")
@app_commands.describe(limite="Quantidade de registros (padrão: 5, máx: 15)")
async def slash_eventos(interaction: discord.Interaction, limite: int = 5):
    await interaction.response.defer()
    embed = await asyncio.to_thread(_get_recent_events_embed, limite)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="ajuda", description="Exibe a lista de comandos disponíveis")
async def slash_ajuda(interaction: discord.Interaction):
    embed = _get_help_embed()
    await interaction.response.send_message(embed=embed)


# ──────────────────────────────────────────────────────────────────────────────
# Comandos de Texto Clássicos (Prefixo !)
# ──────────────────────────────────────────────────────────────────────────────

@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    async with ctx.typing():
        embed = await asyncio.to_thread(_get_worker_status_embed)
        await ctx.reply(embed=embed)


@bot.command(name="placas")
async def cmd_placas(ctx: commands.Context):
    async with ctx.typing():
        embed = await asyncio.to_thread(_get_plates_embed)
        await ctx.reply(embed=embed)


@bot.command(name="bloquear")
async def cmd_bloquear(ctx: commands.Context, placa: str = "", *, motivo: str = ""):
    if not placa:
        await ctx.reply("❌ Por favor informe a placa. Exemplo: `!bloquear ABC1234 Inadimplência`")
        return
    async with ctx.typing():
        autor = ctx.author.display_name
        embed = await asyncio.to_thread(_exec_bloqueio, placa, motivo, autor)
        await ctx.reply(embed=embed)


@bot.command(name="desbloquear")
async def cmd_desbloquear(ctx: commands.Context, placa: str = "", *, motivo: str = ""):
    if not placa:
        await ctx.reply("❌ Por favor informe a placa. Exemplo: `!desbloquear ABC1234 Pagamento confirmado`")
        return
    async with ctx.typing():
        autor = ctx.author.display_name
        embed = await asyncio.to_thread(_exec_desbloqueio, placa, motivo, autor)
        await ctx.reply(embed=embed)


@bot.command(name="eventos")
async def cmd_eventos(ctx: commands.Context, limite: int = 5):
    async with ctx.typing():
        embed = await asyncio.to_thread(_get_recent_events_embed, limite)
        await ctx.reply(embed=embed)


@bot.command(name="ajuda")
async def cmd_ajuda(ctx: commands.Context):
    embed = _get_help_embed()
    await ctx.reply(embed=embed)


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint para iniciar o Bot em Thread
# ──────────────────────────────────────────────────────────────────────────────

def start_discord_bot() -> None:
    """Inicia o Bot do Discord em loop de eventos próprio."""
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        log.info("[DISCORD BOT] DISCORD_BOT_TOKEN não configurado no .env. Bot interativo desativado.")
        return

    log.info("[DISCORD BOT] Iniciando Bot do Discord...")
    try:
        bot.run(token, log_handler=None)
    except Exception as exc:
        log.error("[DISCORD BOT] Erro ao executar o Bot do Discord: %s", exc)
