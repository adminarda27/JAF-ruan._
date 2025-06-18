import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True  # メンバー取得に必要
bot = commands.Bot(command_prefix="!", intents=intents)

LOG_CHANNEL_ID = int(os.getenv("DISCORD_LOG_CHANNEL_ID", 0))
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", 0))
ROLE_ID = int(os.getenv("DISCORD_ROLE_ID", 0))

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

async def send_log(message):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(message)

async def assign_role(user_id):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("⚠️ Guild not found.")
        return

    member = guild.get_member(int(user_id))
    if not member:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception as e:
            print("⚠️ メンバー取得失敗:", e)
            return

    role = guild.get_role(ROLE_ID)
    if role and member:
        try:
            await member.add_roles(role, reason="認証通過により自動付与")
            print(f"✅ {member} にロールを付与しました。")
        except Exception as e:
            print("⚠️ ロール付与失敗:", e)
