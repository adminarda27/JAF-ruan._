from flask import Flask, request, redirect, session, url_for
import requests
import json, os
from datetime import datetime
from dotenv import load_dotenv
from user_agents import parse as parse_ua
from discord_bot import bot

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

def get_ip_info(ip):
    try:
        res = requests.get(f"http://ip-api.com/json/{ip}?fields=66846719").json()
        return {
            "ip": ip,
            "city": res.get("city", "N/A"),
            "region": res.get("regionName", "N/A"),
            "country": res.get("country", "N/A"),
            "lat": res.get("lat"),
            "lon": res.get("lon"),
            "org": res.get("org", "N/A"),
            "proxy": res.get("proxy", False),
            "hosting": res.get("hosting", False)
        }
    except:
        return {"ip": ip, "city": "N/A", "region": "N/A", "country": "N/A"}

def log_access(data):
    if not os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "w") as f:
            json.dump([], f, indent=4)

    with open(ACCESS_LOG_FILE, "r") as f:
        logs = json.load(f)

    logs.append(data)

    with open(ACCESS_LOG_FILE, "w") as f:
        json.dump(logs, f, indent=4)

@app.route("/")
def index():
    return redirect(
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify"
    )

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "No code provided", 400

    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "scope": "identify"
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    r.raise_for_status()
    access_token = r.json().get("access_token")

    user_info = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    ua = parse_ua(request.headers.get("User-Agent"))
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    ip_data = get_ip_info(ip)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log_data = {
        "username": f"{user_info['username']}#{user_info['discriminator']}",
        "id": user_info["id"],
        "ip": ip_data["ip"],
        "location": f"{ip_data['country']} {ip_data['region']} {ip_data['city']}",
        "org": ip_data["org"],
        "lat": ip_data["lat"],
        "lon": ip_data["lon"],
        "proxy": ip_data["proxy"],
        "hosting": ip_data["hosting"],
        "device": f"{ua.device.family} ({ua.os.family} / {ua.browser.family})",
        "timestamp": now
    }

    # ログ保存
    log_access(log_data)

    # Webhook埋め込み送信
    embed = {
        "title": "✅ 新規アクセスログ",
        "color": 0x2ECC71,
        "fields": [
            {"name": "ユーザー", "value": f"{log_data['username']} (`{log_data['id']}`)", "inline": False},
            {"name": "IP / 位置", "value": f"{log_data['ip']} / {log_data['location']}", "inline": True},
            {"name": "組織", "value": log_data["org"], "inline": True},
            {"name": "端末情報", "value": log_data["device"], "inline": False},
            {"name": "マップ", "value": f"[Google Map](https://www.google.com/maps?q={log_data['lat']},{log_data['lon']})", "inline": False},
            {"name": "時間", "value": log_data["timestamp"], "inline": False},
        ]
    }

    requests.post(WEBHOOK_URL, json={"embeds": [embed]})

    # VPN / ホスティング 検知時に警告Embed送信
    if ip_data["proxy"] or ip_data["hosting"]:
        warn_embed = {
            "title": "⚠️ 不審なアクセス検出",
            "color": 0xE74C3C,
            "description": (
                f"**ユーザー:** `{log_data['username']}`\n"
                f"**ID:** `{log_data['id']}`\n"
                f"**IP:** `{log_data['ip']}`\n"
                f"**Proxy:** `{log_data['proxy']}` / Hosting: `{log_data['hosting']}`\n"
                f"📍 [Google Map](https://www.google.com/maps?q={log_data['lat']},{log_data['lon']})"
            )
        }
        requests.post(WEBHOOK_URL, json={"embeds": [warn_embed]})

    # Bot にユーザーID渡してロール付与
    bot.loop.create_task(bot.assign_role(user_info["id"]))

    return f"<h1>認証完了</h1><p>{log_data['username']} でアクセスが記録されました。</p>"

if __name__ == "__main__":
    import threading
    threading.Thread(target=bot.run, args=(os.getenv("DISCORD_BOT_TOKEN"),), daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
