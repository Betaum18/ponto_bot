import datetime
import os

import discord
import pytz

from utils.appscript import call_api
from views.ponto_view import PontoView, build_ponto_embed

TIMEZONE: str = os.getenv("TIMEZONE", "America/Sao_Paulo")


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

        user = interaction.user
        guild = interaction.guild
        tz = pytz.timezone(TIMEZONE)
        now = datetime.datetime.now(tz)
        today = now.strftime("%Y-%m-%d")

        # Redirect if user already has an open session today
        check = await call_api("get_active_thread", user_id=str(user.id), date=today)
        if check.get("success") and check.get("thread_id"):
            existing = guild.get_thread(int(check["thread_id"]))
            if existing:
                await interaction.followup.send(
                    f"Você já tem um ponto aberto hoje! Acesse: {existing.mention}",
                    ephemeral=True,
                )
                return

        # Create private thread
        thread_name = f"Ponto — {user.display_name} — {now.strftime('%d/%m/%Y')}"
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

        # Register session — Apps Script creates the row and returns meta
        reg = await call_api(
            "register_session",
            thread_id=str(thread.id),
            user_id=str(user.id),
            user_name=user.display_name,
            date=today,
        )

        if not reg.get("success"):
            await interaction.followup.send(
                f"❌ Erro ao registrar sessão: {reg.get('error', 'desconhecido')}",
                ephemeral=True,
            )
            await thread.delete()
            return

        meta_horas = reg.get("meta_horas", 8)

        # Pin the control header inside the thread
        embed = build_ponto_embed(user, now, meta_horas)
        view = PontoView()
        header = await thread.send(
            content=f"**Controle de Ponto** | {user.mention} | {now.strftime('%d/%m/%Y')}",
            embed=embed,
            view=view,
        )
        await header.pin()

        await interaction.followup.send(
            f"✅ Thread criada! Acesse: {thread.mention}",
            ephemeral=True,
        )
