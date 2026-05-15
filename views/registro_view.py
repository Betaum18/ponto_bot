import datetime
import os

import discord
import pytz

from utils.appscript import call_api
from views.ponto_view import PontoView, build_ponto_embed

TIMEZONE: str = os.getenv("TIMEZONE", "America/Sao_Paulo")


def get_week_bounds(now: datetime.datetime) -> tuple[str, str]:
    # Python weekday(): Monday=0 … Sunday=6
    # Queremos Sunday=0, então: days_since_sunday = (weekday + 1) % 7
    days_since_sunday = (now.weekday() + 1) % 7
    sunday   = now - datetime.timedelta(days=days_since_sunday)
    saturday = sunday + datetime.timedelta(days=6)
    return sunday.strftime("%Y-%m-%d"), saturday.strftime("%Y-%m-%d")


class RegistroView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📋 Registro",
        style=discord.ButtonStyle.primary,
        custom_id="registro:main",
    )
    async def registro_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        user  = interaction.user
        guild = interaction.guild
        tz    = pytz.timezone(TIMEZONE)
        now   = datetime.datetime.now(tz)

        week_start, week_end = get_week_bounds(now)

        # Redireciona se já existe thread aberta esta semana
        check = await call_api("get_active_thread", user_id=str(user.id), week_start=week_start)
        if check.get("success") and check.get("thread_id"):
            existing = guild.get_thread(int(check["thread_id"]))
            if existing is None:
                try:
                    existing = await guild.fetch_channel(int(check["thread_id"]))
                except (discord.NotFound, discord.Forbidden):
                    existing = None

            if existing is not None:
                await interaction.followup.send(
                    f"Você já tem uma thread de ponto aberta esta semana!\n"
                    f"➡️ {existing.mention}\n{existing.jump_url}",
                    ephemeral=True,
                )
                return

        # Nome da thread com o intervalo da semana
        start_dt = datetime.datetime.strptime(week_start, "%Y-%m-%d")
        end_dt   = datetime.datetime.strptime(week_end,   "%Y-%m-%d")
        thread_name = (
            f"Ponto — {user.display_name} — "
            f"{start_dt.strftime('%d/%m')} a {end_dt.strftime('%d/%m')}"
        )

        try:
            thread = await interaction.channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Sem permissão para criar threads privadas neste canal.\n"
                "O bot precisa da permissão **Criar Threads Privadas**.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Erro ao criar thread: {exc}", ephemeral=True)
            return

        await thread.add_user(user)

        # Adiciona todos os membros com cargo ADM à thread
        adm_role_id = int(os.getenv("ADM_ROLE_ID", "0"))
        if adm_role_id:
            adm_role = guild.get_role(adm_role_id)
            if adm_role:
                for member in adm_role.members:
                    if member.id != user.id:
                        try:
                            await thread.add_user(member)
                        except discord.HTTPException:
                            pass

        # Registra sessão semanal no Sheets
        reg = await call_api(
            "register_session",
            thread_id=str(thread.id),
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
            await thread.delete()
            return

        meta_horas = reg.get("meta_horas", 5)

        # Cabeçalho fixado na thread
        embed = build_ponto_embed(
            user,
            week_start=week_start,
            week_end=week_end,
            meta_horas=meta_horas,
        )
        view   = PontoView()
        header = await thread.send(
            content=f"**Controle de Ponto Semanal** | {user.mention}",
            embed=embed,
            view=view,
        )
        await header.pin()

        await interaction.followup.send(
            f"✅ Thread de ponto criada!\n"
            f"➡️ {thread.mention}\n{thread.jump_url}",
            ephemeral=True,
        )
