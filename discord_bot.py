import os
import discord
from discord.ext import commands
import aiohttp  # 追加

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

# ここから追加部分 ↓↓↓

# ユーザーID(str) → アクセストークン(str) を保存する辞書（本来はDB推奨）
user_tokens = {}

@bot.command()
@commands.has_permissions(administrator=True)
async def add(ctx, subcommand=None, user_id=None, guild_id=None):
    if subcommand != "user" or user_id is None or guild_id is None:
        await ctx.send("使い方: !add user <ユーザーID> <サーバーID>")
        return

    token = user_tokens.get(user_id)
    if not token:
        await ctx.send(f"ユーザー {user_id} のアクセストークンが登録されていません。")
        return

    url = f"https://discord.com/api/guilds/{guild_id}/members/{user_id}"
    headers = {
        "Authorization": f"Bot {os.getenv('DISCORD_BOT_TOKEN')}",
        "Content-Type": "application/json"
    }
    json_data = {"access_token": token}

    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=json_data) as resp:
            if resp.status in [201, 204]:
                await ctx.send(f"ユーザー {user_id} をサーバー {guild_id} に追加しました！")
            else:
                text = await resp.text()
                await ctx.send(f"追加失敗: {resp.status} {text}")

# ▼ ここからスラッシュコマンド追加（discord.py 2.0+が必要）

from discord import app_commands

class SlashCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_tokens = user_tokens  # 共有して使う

    @app_commands.command(name="adduser", description="ユーザーをサーバーに追加します")
    @app_commands.describe(user_id="追加したいユーザーID", guild_id="サーバーID")
    async def adduser(self, interaction: discord.Interaction, user_id: str, guild_id: str):
        token = self.user_tokens.get(user_id)
        if not token:
            await interaction.response.send_message(f"ユーザー {user_id} のアクセストークンが登録されていません。", ephemeral=True)
            return

        url = f"https://discord.com/api/guilds/{guild_id}/members/{user_id}"
        headers = {
            "Authorization": f"Bot {os.getenv('DISCORD_BOT_TOKEN')}",
            "Content-Type": "application/json"
        }
        json_data = {"access_token": token}

        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json=json_data) as resp:
                if resp.status in [201, 204]:
                    await interaction.response.send_message(f"ユーザー {user_id} をサーバー {guild_id} に追加しました！")
                else:
                    text = await resp.text()
                    await interaction.response.send_message(f"追加失敗: {resp.status} {text}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SlashCommands(bot))

# 既存の bot.run はそのまま
