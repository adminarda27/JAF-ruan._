from flask import Flask, request, render_template, jsonify, redirect
import requests
import json
import os
import threading
from dotenv import load_dotenv
from discord_bot import bot

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

def get_geo_info(ip):
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}?lang=ja")
        data = response.json()
        return {
            "country": data.get("country", "不明"),
            "region": data.get("regionName", "不明")
        }
    except:
        return {"country": "不明", "region": "不明"}

def save_log(discord_id, data):
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}

    logs[discord_id] = data
    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

@app.route("/")
def index():
    discord_auth_url = (
        f"https://discord.com/oauth2/authorize?"
        f"client_id={DISCORD_CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=identify"
    )
    return render_template("index.html", discord_auth_url=discord_auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify"
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_response = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    token_json = token_response.json()
    access_token = token_json.get("access_token")

    if not access_token:
        return "アクセストークン取得失敗", 400

    user_info = requests.get("https://discord.com/api/users/@me", headers={
        "Authorization": f"Bearer {access_token}"
    }).json()

    ip = get_client_ip()
    if ip.startswith(("127.", "192.", "10.", "172.")):
        ip = requests.get("https://api.ipify.org").text

    geo = get_geo_info(ip)
    user_agent = request.headers.get("User-Agent", "不明")

    user_data = {
        "username": f"{user_info['username']}#{user_info['discriminator']}",
        "id": user_info["id"],
        "ip": ip,
        "country": geo["country"],
        "region": geo["region"],
        "user_agent": user_agent
    }

    save_log(user_info["id"], user_data)

    bot.loop.create_task(bot.send_log(
        f"✅ 新しいアクセスログ:\n```名前: {user_data['username']}\nID: {user_data['id']}\nIP: {ip}\n国: {geo['country']}\n地域: {geo['region']}\nUA: {user_agent}```"
    ))

    return f"✅ ようこそ {user_data['username']} さん！（ID: {user_data['id']}）"

@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}
    return render_template("logs.html", logs=logs)

def run_bot():
    bot.run(os.getenv("DISCORD_BOT_TOKEN"))

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(debug=True, host="0.0.0.0", port=5000)
