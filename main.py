import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from views.ponto_view import PontoView
from views.registro_view import RegistroView

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    # Register persistent views so buttons survive restarts
    bot.add_view(RegistroView())
    bot.add_view(PontoView())
    await bot.tree.sync()
    print(f"✅ {bot.user} online!")


async def main() -> None:
    async with bot:
        await bot.load_extension("cogs.ponto")
        await bot.start(os.environ["DISCORD_TOKEN"])


asyncio.run(main())
