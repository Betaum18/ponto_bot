import datetime
import os

import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks

from utils.appscript import call_api
from views.ponto_view import PontoView, build_ponto_embed, fmt_horas, _fmt_week
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
        self.saturday_close.start()

    def cog_unload(self) -> None:
        self.saturday_close.cancel()

    # ── Task: encerramento automático sábado 12h ──────────────────────────────

    # 12h BRT = 15h UTC (Brasil não usa horário de verão desde 2019)
    @tasks.loop(time=datetime.time(hour=15, minute=0))
    async def saturday_close(self) -> None:
        tz = pytz.timezone(TIMEZONE)
        if datetime.datetime.now(tz).weekday() != 5:  # 5 = sábado
            return

        result = await call_api("close_all_week")
        if not result.get("success"):
            return

        for session in result.get("closed", []):
            await self._notify_thread_close(session)

    @saturday_close.before_loop
    async def before_saturday_close(self) -> None:
        await self.bot.wait_until_ready()

    async def _notify_thread_close(self, session: dict) -> None:
        thread = self.bot.get_channel(int(session["thread_id"]))
        if not isinstance(thread, discord.Thread):
            return

        member = thread.guild.get_member(int(session["user_id"]))
        if not member:
            return

        horas  = session["horas_semana"]
        meta   = session["meta_horas"]
        status = session["status"]

        # Atualiza embed fixado
        try:
            pins = await thread.pins()
            for pin in pins:
                if pin.author.bot and pin.embeds:
                    embed = build_ponto_embed(
                        member,
                        week_start=session["week_start"],
                        week_end=session["week_end"],
                        meta_horas=meta,
                        status=status,
                        horas_semana=horas,
                    )
                    closed_view = PontoView()
                    for item in closed_view.children:
                        if hasattr(item, "custom_id") and item.custom_id == "ponto:justificativa":
                            item.disabled = (status != "incompleto")
                        else:
                            item.disabled = True
                    await pin.edit(embed=embed, view=closed_view)
                    break
        except discord.HTTPException:
            pass

        # Envia alerta na thread
        try:
            week_fmt = _fmt_week(session["week_start"], session["week_end"])
            if status == "incompleto":
                diff = meta - horas
                await thread.send(
                    f"⏰ **Encerramento automático — Sábado 12h**\n"
                    f"⚠️ {member.mention} a meta semanal não foi atingida!\n"
                    f"> Semana: **{week_fmt}**\n"
                    f"> Trabalhado: **{fmt_horas(horas)}** | Meta: **{fmt_horas(meta)}** | "
                    f"Faltaram: **{fmt_horas(diff)}**\n"
                    f"Clique em **📝 Justificativa** para registrar o motivo."
                )
            else:
                await thread.send(
                    f"⏰ **Encerramento automático — Sábado 12h**\n"
                    f"🎉 {member.mention} semana concluída! "
                    f"Total: **{fmt_horas(horas)}** — Meta atingida!"
                )
        except discord.HTTPException:
            pass

        # Exclui a thread após notificação (mantém aberta só se incompleto, aguardando justificativa)
        if status in ("fechado", "justificado"):
            try:
                await thread.delete()
            except discord.HTTPException:
                pass

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
            icon      = _STATUS_ICONS.get(u["today_status"], "❓")
            horas     = u["today_horas"]
            meta      = u["meta_horas"]
            status    = u["today_status"]
            remaining = max(0.0, meta - horas)

            if status in ("fechado", "justificado"):
                extra = "✅ Meta atingida" if horas >= meta else f"Faltaram {fmt_horas(remaining)}"
            elif status == "incompleto":
                extra = f"⚠️ Faltaram {fmt_horas(remaining)}"
            elif status == "ausente":
                extra = f"Faltam {fmt_horas(remaining)} para a meta"
            elif remaining <= 0:
                extra = "✅ Meta atingida"
            else:
                extra = f"Faltam **{fmt_horas(remaining)}** para a meta"

            lines.append(
                f"{icon} **{u['user_name']}**\n"
                f"  ⏱️ {fmt_horas(horas)} trabalhadas / 🎯 Meta: {fmt_horas(meta)} — {extra}"
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
