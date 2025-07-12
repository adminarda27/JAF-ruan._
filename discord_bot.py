import os
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# 🔁 サーバーごとの設定
LOG_CHANNELS = {
    "A": int(os.getenv("LOG_CHANNEL_A", 0)),
    "B": int(os.getenv("LOG_CHANNEL_B", 0))
}

GUILD_IDS = {
    "A": int(os.getenv("GUILD_A_ID", 0)),
    "B": int(os.getenv("GUILD_B_ID", 0))
}

ROLE_IDS = {
    "A": int(os.getenv("ROLE_A_ID", 0)),
    "B": int(os.getenv("ROLE_B_ID", 0))
}

user_tokens = {}

@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"❌ Sync error: {e}")

# ✅ ログ送信（source_guildごとに切り替え）
async def send_log(content=None, embed=None, source_guild="A"):
    channel_id = LOG_CHANNELS.get(source_guild)
    if not channel_id:
        print(f"⚠️ ログチャンネル未設定: {source_guild}")
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        print(f"⚠️ チャンネル取得失敗: {channel_id}")
        return

    if embed:
        embed_obj = discord.Embed(
            title=embed.get("title", "ログ"),
            description=embed.get("description", ""),
            color=0x00ff00
        )
        if "thumbnail" in embed and embed["thumbnail"]:
            embed_obj.set_thumbnail(url=embed["thumbnail"]["url"])
        await channel.send(embed=embed_obj)
    elif content:
        await channel.send(content)

# ✅ ロール付与（source_guildでギルド・ロール両方を切り替える）
async def assign_role(user_id, source_guild="A"):
    guild_id = GUILD_IDS.get(source_guild)
    role_id = ROLE_IDS.get(source_guild)

    if not guild_id or not role_id:
        print(f"⚠️ ギルドまたはロールID未設定: {source_guild}")
        return

    guild = bot.get_guild(guild_id)
    if not guild:
        print(f"⚠️ Guild取得失敗: {guild_id}")
        return

    member = guild.get_member(int(user_id))
    if not member:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception as e:
            print(f"⚠️ メンバー取得失敗: {e}")
            return

    role = guild.get_role(role_id)
    if role and member:
        try:
            await member.add_roles(role, reason="認証完了により自動付与")
            print(f"✅ {member} にロールを付与しました")
        except Exception as e:
            print(f"⚠️ ロール付与失敗: {e}")

# Flaskから使えるように登録
bot.send_log = send_log
bot.assign_role = assign_role
