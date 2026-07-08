import datetime
import os

import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks

from utils.appscript import call_api
from views.ponto_view import PontoView, build_ponto_embed, fmt_horas, _fmt_week

TIMEZONE: str = os.getenv("TIMEZONE", "America/Sao_Paulo")
ADM_ROLE_ID: int = int(os.getenv("ADM_ROLE_ID", "0"))


def get_week_bounds(now: datetime.datetime) -> tuple[str, str]:
    # Python weekday(): Monday=0 … Sunday=6
    # Queremos Sunday=0, então: days_since_sunday = (weekday + 1) % 7
    days_since_sunday = (now.weekday() + 1) % 7
    sunday   = now - datetime.timedelta(days=days_since_sunday)
    saturday = sunday + datetime.timedelta(days=6)
    return sunday.strftime("%Y-%m-%d"), saturday.strftime("%Y-%m-%d")

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

    admin = app_commands.Group(name="admin", description="Administração de ponto")

    # ── /ponto ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="ponto",
        description="Publica o painel de controle de ponto neste canal",
    )
    async def ponto(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        user  = interaction.user
        guild = interaction.guild
        tz    = pytz.timezone(TIMEZONE)
        now   = datetime.datetime.now(tz)
        week_start, week_end = get_week_bounds(now)

        reg = await call_api(
            "register_session",
            thread_id=str(channel.id),
            user_id=str(user.id),
            user_name=user.display_name,
            week_start=week_start,
            week_end=week_end,
        )

        if not reg.get("success"):
            await interaction.followup.send(
                f"❌ Erro ao registrar sessão: {reg.get('error', 'desconhecido')}",
                ephemeral=True,
            )
            return

        if reg.get("already_exists"):
            existing_thread_id = int(reg["existing_thread_id"])
            if existing_thread_id == channel.id:
                await interaction.followup.send(
                    "✅ O painel de ponto já está publicado neste canal.",
                    ephemeral=True,
                )
                return

            existing = guild.get_channel_or_thread(existing_thread_id)
            if existing is None:
                try:
                    existing = await guild.fetch_channel(existing_thread_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    existing = None

            if existing:
                await interaction.followup.send(
                    f"Você já tem um canal de ponto aberto esta semana!\n"
                    f"➡️ {existing.mention}\n{existing.jump_url}",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Você já tem uma sessão aberta esta semana, mas não foi possível localizar o canal.",
                    ephemeral=True,
                )
            return

        # Adiciona membros com cargo ADM à thread (não se aplica a canais normais)
        if ADM_ROLE_ID and isinstance(channel, discord.Thread):
            adm_role = guild.get_role(ADM_ROLE_ID)
            if adm_role:
                for member in adm_role.members:
                    if member.id != user.id:
                        try:
                            await channel.add_user(member)
                        except discord.HTTPException:
                            pass

        meta_horas = reg.get("meta_horas", 5)
        embed = build_ponto_embed(user, week_start=week_start, week_end=week_end, meta_horas=meta_horas)
        header = await channel.send(
            content=f"**Controle de Ponto Semanal** | {user.mention}",
            embed=embed,
            view=PontoView(),
        )
        await header.pin()

        await interaction.followup.send("✅ Painel de ponto publicado!", ephemeral=True)

    # ── /admin usuarios ───────────────────────────────────────────────────────

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

    # ── /admin setmeta ────────────────────────────────────────────────────────

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

    # ── /admin relatorio ──────────────────────────────────────────────────────

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
