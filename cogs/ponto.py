import datetime
import os

import discord
import pytz
from discord import app_commands
from discord.ext import commands

from utils.appscript import call_api
from views.ponto_view import build_ponto_embed, fmt_horas
from views.registro_view import RegistroView

TIMEZONE: str = os.getenv("TIMEZONE", "America/Sao_Paulo")
ADM_ROLE_ID: int = int(os.getenv("ADM_ROLE_ID", "0"))

_STATUS_ICONS = {
    "aberto":      "⚪",
    "ativo":       "🟢",
    "pausado":     "🟡",
    "fechado":     "✅",
    "incompleto":  "🔴",
    "justificado": "📝",
    "ausente":     "⚫",
}


def _is_adm(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    return bool(ADM_ROLE_ID and any(r.id == ADM_ROLE_ID for r in interaction.user.roles))


def require_adm():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not _is_adm(interaction):
            await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


class PontoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    ponto = app_commands.Group(name="ponto", description="Controle de ponto")
    admin = app_commands.Group(
        name="admin",
        description="Administração de ponto",
        parent=ponto,
    )

    # ── /ponto setup ──────────────────────────────────────────────────────────

    @ponto.command(name="setup", description="Envia o embed de registro no canal atual")
    @require_adm()
    async def setup(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="📋 Sistema de Controle de Ponto",
            description=(
                "Bem-vindo ao sistema de ponto!\n\n"
                "Clique em **Registro** para abrir sua thread semanal de ponto.\n"
                "A semana vai de **domingo a sábado** com meta de **5 horas semanais**.\n\n"
                "**Como funciona:**\n"
                "▶️ **Iniciar Ponto** — Começa a sessão do dia\n"
                "⏸️ **Pausar / Retomar** — Pausa ou retoma\n"
                "⏹️ **Fechar Ponto** — Encerra a sessão do dia\n"
                "📝 **Justificativa** — Necessária se a semana encerrar sem atingir a meta"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Clique no botão abaixo para registrar seu ponto")

        await interaction.channel.send(embed=embed, view=RegistroView())
        await interaction.response.send_message("✅ Embed enviado!", ephemeral=True)

    # ── /ponto admin usuarios ─────────────────────────────────────────────────

    @admin.command(name="usuarios", description="Lista todos os usuários e status do dia")
    @require_adm()
    async def usuarios(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        result = await call_api("get_all_users")
        if not result.get("success"):
            await interaction.followup.send(f"❌ {result.get('error')}", ephemeral=True)
            return

        users = result.get("users", [])
        if not users:
            await interaction.followup.send("Nenhum usuário registrado.", ephemeral=True)
            return

        lines = []
        for u in users:
            icon = _STATUS_ICONS.get(u["today_status"], "❓")
            lines.append(
                f"{icon} **{u['user_name']}** — "
                f"{fmt_horas(u['today_horas'])} / {fmt_horas(u['meta_horas'])} "
                f"({u['today_status']})"
            )

        tz = pytz.timezone(TIMEZONE)
        embed = discord.Embed(
            title="👥 Usuários — Status da Semana",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Atualizado em {datetime.datetime.now(tz).strftime('%d/%m/%Y %H:%M')}"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /ponto admin setmeta ──────────────────────────────────────────────────

    @admin.command(name="setmeta", description="Define a meta de horas para um usuário")
    @app_commands.describe(usuario="Usuário alvo", horas="Meta em horas (ex: 8 ou 6.5)")
    @require_adm()
    async def setmeta(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        horas: float,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        result = await call_api("set_meta", user_id=str(usuario.id), meta_horas=horas)
        if result.get("success"):
            await interaction.followup.send(
                f"✅ Meta de **{fmt_horas(horas)}** definida para {usuario.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"❌ {result.get('error', 'Usuário não encontrado.')}",
                ephemeral=True,
            )

    # ── /ponto admin relatorio ────────────────────────────────────────────────

    @admin.command(name="relatorio", description="Exibe o histórico de ponto de um usuário")
    @app_commands.describe(usuario="Usuário para consultar")
    @require_adm()
    async def relatorio(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        result = await call_api("get_records", user_id=str(usuario.id), limit=10)
        if not result.get("success"):
            await interaction.followup.send(f"❌ {result.get('error')}", ephemeral=True)
            return

        records = result.get("records", [])
        if not records:
            await interaction.followup.send(
                f"Nenhum registro encontrado para {usuario.mention}.",
                ephemeral=True,
            )
            return

        lines = []
        for r in records:
            icon = _STATUS_ICONS.get(r["status"], "❓")
            lines.append(
                f"{icon} `{r['week_start']} → {r['week_end']}` — "
                f"**{fmt_horas(r['horas_semana'])}** / {fmt_horas(r['meta_horas'])}"
            )
            if r.get("justificativa"):
                short = r["justificativa"][:80]
                if len(r["justificativa"]) > 80:
                    short += "..."
                lines.append(f"  > 📝 {short}")

        embed = discord.Embed(
            title=f"📊 Relatório — {usuario.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PontoCog(bot))
