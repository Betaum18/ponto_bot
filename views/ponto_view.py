import datetime
import os

import discord
import pytz

from utils.appscript import call_api

TIMEZONE: str = os.getenv("TIMEZONE", "America/Sao_Paulo")
ADM_ROLE_ID: int = int(os.getenv("ADM_ROLE_ID", "0"))

_STATUS_MAP = {
    "aberto":      ("⚪ Aguardando Início",                             discord.Color.light_grey()),
    "ativo":       ("🟢 Ativo",                                         discord.Color.green()),
    "pausado":     ("🟡 Pausado",                                       discord.Color.yellow()),
    "fechado":     ("✅ Semana Concluída",                               discord.Color.blue()),
    "incompleto":  ("🔴 Meta Não Atingida — Justificativa Necessária",  discord.Color.red()),
    "justificado": ("📝 Justificado",                                   discord.Color.purple()),
}


def fmt_horas(horas: float) -> str:
    h = int(horas)
    m = int(round((horas - h) * 60))
    return f"{h}h {m:02d}min"


def _fmt_week(week_start: str, week_end: str) -> str:
    try:
        s = datetime.datetime.strptime(week_start, "%Y-%m-%d")
        e = datetime.datetime.strptime(week_end,   "%Y-%m-%d")
        return f"{s.strftime('%d/%m')} (Dom) — {e.strftime('%d/%m')} (Sáb)"
    except Exception:
        return f"{week_start} — {week_end}"


def _parse_inicio(inicio_iso: str | None) -> str | None:
    if not inicio_iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(inicio_iso.replace("Z", "+00:00"))
        return dt.astimezone(pytz.timezone(TIMEZONE)).strftime("%H:%M")
    except Exception:
        return inicio_iso


def build_ponto_embed(
    user: discord.Member | discord.User,
    week_start: str,
    week_end: str,
    meta_horas: float,
    status: str = "aberto",
    session_inicio: str | None = None,
    horas_semana: float = 0.0,
) -> discord.Embed:
    label, color = _STATUS_MAP.get(status, _STATUS_MAP["aberto"])

    embed = discord.Embed(title="🕐 Controle de Ponto Semanal", color=color)
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)

    embed.add_field(name="📅 Semana",           value=_fmt_week(week_start, week_end), inline=False)
    embed.add_field(name="📊 Status",           value=label,                           inline=True)
    embed.add_field(name="⏱️ Horas na Semana",  value=fmt_horas(horas_semana),         inline=True)
    embed.add_field(name="🎯 Meta Semanal",     value=fmt_horas(meta_horas),           inline=True)

    inicio_display = _parse_inicio(session_inicio)
    if inicio_display:
        embed.add_field(name="🕐 Sessão Atual", value=f"Desde {inicio_display}", inline=True)

    embed.set_footer(text="Use os botões abaixo para controlar seu ponto")
    return embed


class JustificativaModal(discord.ui.Modal, title="📝 Justificativa de Ponto"):
    texto = discord.ui.TextInput(
        label="Justificativa",
        style=discord.TextStyle.paragraph,
        placeholder="Descreva o motivo pelo qual não atingiu a meta de 5h esta semana...",
        min_length=10,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        result = await call_api(
            "justify",
            thread_id=str(interaction.channel.id),
            user_id=str(interaction.user.id),
            justificativa=self.texto.value,
        )

        if not result.get("success"):
            await interaction.followup.send(
                f"❌ {result.get('error', 'Erro ao registrar justificativa.')}",
                ephemeral=True,
            )
            return

        new_status = result.get("status")

        # Se a semana já estava incompleta, trava os botões e atualiza embed
        if new_status == "justificado":
            owner = await _resolve_owner(interaction, result.get("user_id"))
            embed = build_ponto_embed(
                owner,
                week_start=result.get("week_start", ""),
                week_end=result.get("week_end", ""),
                meta_horas=result.get("meta_horas", 5),
                status="justificado",
                horas_semana=result.get("horas_semana", 0),
            )
            view = PontoView()
            _disable_all(view)
            pins = await interaction.channel.pins()
            for pin in pins:
                if pin.author.bot and pin.embeds:
                    await pin.edit(embed=embed, view=view)
                    break
            await interaction.followup.send("✅ Justificativa registrada!", ephemeral=True)
        else:
            # Justificativa antecipada — semana ainda em aberto
            await interaction.followup.send(
                "✅ Justificativa antecipada registrada! "
                "Você pode continuar registrando horas normalmente.",
                ephemeral=True,
            )


async def _resolve_owner(
    interaction: discord.Interaction,
    user_id: str | None,
) -> discord.Member | discord.User:
    if user_id and interaction.guild:
        member = interaction.guild.get_member(int(user_id))
        if member:
            return member
    return interaction.user


def _disable_all(view: discord.ui.View) -> None:
    for item in view.children:
        item.disabled = True


class PontoView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    # ── auth ──────────────────────────────────────────────────────────────────

    async def _fetch_and_check(
        self, interaction: discord.Interaction
    ) -> dict | None:
        result = await call_api("get_status", thread_id=str(interaction.channel.id))

        if not result.get("success"):
            await interaction.followup.send("❌ Sessão não encontrada.", ephemeral=True)
            return None

        is_owner = str(interaction.user.id) == str(result.get("user_id", ""))
        is_adm = bool(
            ADM_ROLE_ID and any(r.id == ADM_ROLE_ID for r in interaction.user.roles)
        ) or interaction.user.guild_permissions.administrator

        if not is_owner and not is_adm:
            await interaction.followup.send(
                "❌ Apenas o dono do ponto ou ADMs podem usar estes botões.",
                ephemeral=True,
            )
            return None

        return result

    # ── embed updater ─────────────────────────────────────────────────────────

    async def _refresh_embed(
        self,
        interaction: discord.Interaction,
        result: dict,
        status: str,
        view: discord.ui.View | None = None,
    ) -> None:
        owner = await _resolve_owner(interaction, result.get("user_id"))
        embed = build_ponto_embed(
            owner,
            week_start=result.get("week_start", ""),
            week_end=result.get("week_end", ""),
            meta_horas=result.get("meta_horas", 5),
            status=status,
            session_inicio=result.get("session_inicio"),
            horas_semana=result.get("horas_semana", 0.0),
        )
        await interaction.message.edit(embed=embed, view=view or self)

    # ── buttons ───────────────────────────────────────────────────────────────

    @discord.ui.button(
        label="▶️ Iniciar Ponto",
        style=discord.ButtonStyle.success,
        custom_id="ponto:iniciar",
        row=0,
    )
    async def iniciar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self._fetch_and_check(interaction)
        if result is None:
            return

        if result.get("status") != "aberto":
            await interaction.followup.send(
                f"❌ Não é possível iniciar o ponto agora. (status: `{result.get('status')}`)",
                ephemeral=True,
            )
            return

        start = await call_api("start", thread_id=str(interaction.channel.id), user_id=str(interaction.user.id))
        if not start.get("success"):
            await interaction.followup.send(f"❌ {start.get('error', 'Erro ao iniciar.')}", ephemeral=True)
            return

        await self._refresh_embed(interaction, start, "ativo")
        await interaction.followup.send("✅ Ponto iniciado!", ephemeral=True)

    @discord.ui.button(
        label="⏸️ Pausar / ▶️ Retomar",
        style=discord.ButtonStyle.secondary,
        custom_id="ponto:pausar",
        row=0,
    )
    async def pausar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self._fetch_and_check(interaction)
        if result is None:
            return

        current = result.get("status")
        if current == "ativo":
            action, new_status, msg = "pause", "pausado", "⏸️ Ponto pausado!"
        elif current == "pausado":
            action, new_status, msg = "resume", "ativo", "▶️ Ponto retomado!"
        else:
            await interaction.followup.send("❌ Ponto não está ativo para pausar/retomar.", ephemeral=True)
            return

        op = await call_api(action, thread_id=str(interaction.channel.id), user_id=str(interaction.user.id))
        if not op.get("success"):
            await interaction.followup.send(f"❌ {op.get('error', 'Erro na operação.')}", ephemeral=True)
            return

        await self._refresh_embed(interaction, op, new_status)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(
        label="⏹️ Fechar Ponto",
        style=discord.ButtonStyle.danger,
        custom_id="ponto:fechar",
        row=0,
    )
    async def fechar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self._fetch_and_check(interaction)
        if result is None:
            return

        if result.get("status") not in ("ativo", "pausado"):
            await interaction.followup.send("❌ O ponto não está ativo.", ephemeral=True)
            return

        close = await call_api("close", thread_id=str(interaction.channel.id), user_id=str(interaction.user.id))
        if not close.get("success"):
            await interaction.followup.send(f"❌ {close.get('error', 'Erro ao fechar.')}", ephemeral=True)
            return

        horas_semana = close.get("horas_semana", 0.0)
        meta         = close.get("meta_horas", 5.0)
        status       = close.get("status")
        is_saturday  = close.get("is_saturday", False)
        week_fmt     = _fmt_week(close.get("week_start", ""), close.get("week_end", ""))

        # Monta view com botões adequados ao novo estado
        closed_view = PontoView()
        _ALWAYS_ON = {"ponto:iniciar", "ponto:justificativa"}
        if status == "aberto":
            # Semana continua: Iniciar e Justificativa habilitados
            for item in closed_view.children:
                if hasattr(item, "custom_id"):
                    item.disabled = item.custom_id not in _ALWAYS_ON
        elif status == "fechado":
            # Meta atingida: tudo desabilitado
            _disable_all(closed_view)
        else:
            # incompleto / justificado: só Justificativa
            for item in closed_view.children:
                if hasattr(item, "custom_id"):
                    item.disabled = item.custom_id != "ponto:justificativa"

        await self._refresh_embed(interaction, close, status, view=closed_view)

        if status == "incompleto":
            diff = meta - horas_semana
            await interaction.followup.send(
                f"⚠️ {interaction.user.mention} a meta semanal não foi atingida!\n"
                f"> Semana: **{week_fmt}**\n"
                f"> Trabalhado: **{fmt_horas(horas_semana)}** | Meta: **{fmt_horas(meta)}** | "
                f"Faltaram: **{fmt_horas(diff)}**\n"
                f"Por favor, clique em **📝 Justificativa** para registrar o motivo.",
                ephemeral=False,
            )
        elif status == "fechado":
            await interaction.followup.send(
                f"🎉 Semana encerrada! Total: **{fmt_horas(horas_semana)}**. "
                f"Meta de **{fmt_horas(meta)}** atingida!",
                ephemeral=False,
            )
        else:
            remaining = max(0.0, meta - horas_semana)
            msg = (
                f"✅ Sessão encerrada! Total da semana: **{fmt_horas(horas_semana)}** / **{fmt_horas(meta)}**"
            )
            if remaining > 0:
                msg += f" — Faltam **{fmt_horas(remaining)}** para a meta."
            else:
                msg += " — Meta já atingida! 🎉"
            await interaction.followup.send(msg, ephemeral=False)

    @discord.ui.button(
        label="📝 Justificativa",
        style=discord.ButtonStyle.primary,
        custom_id="ponto:justificativa",
        row=1,
    )
    async def justificativa(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        status_result = await call_api("get_status", thread_id=str(interaction.channel.id))

        if not status_result.get("success"):
            await interaction.response.send_message("❌ Sessão não encontrada.", ephemeral=True)
            return

        is_owner = str(interaction.user.id) == str(status_result.get("user_id", ""))
        is_adm = bool(
            ADM_ROLE_ID and any(r.id == ADM_ROLE_ID for r in interaction.user.roles)
        ) or interaction.user.guild_permissions.administrator

        if not is_owner and not is_adm:
            await interaction.response.send_message(
                "❌ Apenas o dono do ponto ou ADMs podem registrar justificativa.",
                ephemeral=True,
            )
            return

        if status_result.get("status") in ("fechado", "justificado"):
            await interaction.response.send_message(
                "❌ Semana já encerrada — não é possível registrar justificativa.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(JustificativaModal())
