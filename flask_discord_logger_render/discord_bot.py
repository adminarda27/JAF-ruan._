
import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.message_content = True

LOG_CHANNEL_ID = int(os.getenv("DISCORD_LOG_CHANNEL_ID"))
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

async def send_log(message: str):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(message)

bot.send_log = send_log
