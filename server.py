from http.server import HTTPServer, BaseHTTPRequestHandler
import json, urllib.request, urllib.parse, os

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID    = os.environ.get("CHANNEL_ID", "")
DISCUSSION_ID = os.environ.get("DISCUSSION_ID", "")
MINI_APP_URL  = os.environ.get("MINI_APP_URL", "https://t.me/designindexxx_bot/rate")
API           = f"https://api.telegram.org/bot{BOT_TOKEN}"

MAP_FILE  = "/tmp/post_map.json"   # channel_post_id  → discussion_thread_id
SLUG_FILE = "/tmp/slug_map.json"   # slug              → channel_post_id

# ── persistence ───────────────────────────────────────────────────────────────

def load_map():
    if os.path.exists(MAP_FILE):
        try:
            with open(MAP_FILE) as f:
                return {int(k): v for k, v in json.load(f).items()}
        except:
            pass
    return {}

def save_map(m):
    with open(MAP_FILE, "w") as f:
        json.dump(m, f)

def load_slug_map():
    if os.path.exists(SLUG_FILE):
        try:
            with open(SLUG_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

def save_slug_map(m):
    with open(SLUG_FILE, "w") as f:
        json.dump(m, f)

POST_MAP = load_map()       # {channel_post_id: discussion_thread_id}
SLUG_MAP = load_slug_map()  # {slug: channel_post_id}

# ── Telegram API helper ───────────────────────────────────────────────────────

def tg(method, data):
    req = urllib.request.Request(
        f"{API}/{method}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:
        print("TG error:", e)
        return None

# ── comment formatting ────────────────────────────────────────────────────────

def score_bar(val, max_val=10):
    filled = round(val / max_val * 5)
    return "●" * filled + "○" * (5 - filled)

def format_comment(r):
    s        = r.get("scores", {})
    username = r.get("username")
    name     = r.get("name", "Аноним")
    final    = r.get("final", 0)
    comment  = r.get("comment", "").strip()
    mention  = f"@{username}" if username else name
    lines = [
        f"👤 {mention}", "",
        f"{'⭐️' * round(final/10)} {final}/100", "",
        f"🎯 Содержание   {score_bar(s.get('content',0))}  {s.get('content','—')}",
        f"🧭 Удобство       {score_bar(s.get('usability',0))}  {s.get('usability','—')}",
        f"✦  Визуал           {score_bar(s.get('visual',0))}  {s.get('visual','—')}",
        f"💡 Идея              {score_bar(s.get('idea',0))}  {s.get('idea','—')}",
    ]
    if comment:
        lines += ["", f"💬 {comment}"]
    return "\n".join(lines)

# ── ID helpers ────────────────────────────────────────────────────────────────

def parse_channel_post_id(post_id):
    """'post_001_15' → 15  (legacy format, kept for compatibility)"""
    parts = post_id.split("_")
    if len(parts) >= 3:
        try:
            return int(parts[-1])
        except:
            pass
    return None

def resolve_discussion_thread(post_id):
    """
    Resolve discussion_thread_id from any postId format:
      - slug  (e.g. 'sber')         → SLUG_MAP[slug] → POST_MAP[channel_id]
      - legacy (e.g. 'post_001_15') → POST_MAP[15]
    """
    # Try slug lookup first
    channel_post_id = SLUG_MAP.get(post_id)
    if channel_post_id:
        return POST_MAP.get(channel_post_id)
    # Fall back to legacy numeric format
    channel_post_id = parse_channel_post_id(post_id)
    if channel_post_id:
        return POST_MAP.get(channel_post_id)
    return None

# ── webhook handler ───────────────────────────────────────────────────────────

def handle_telegram_update(update):
    print(f"TG update received: {json.dumps(update)[:500]}")
    msg = update.get("message")
    if not msg:
        return
    if msg.get("is_automatic_forward"):
        channel_post_id   = msg.get("forward_from_message_id")
        discussion_msg_id = msg.get("message_id")
        if channel_post_id and discussion_msg_id:
            POST_MAP[channel_post_id] = discussion_msg_id
            save_map(POST_MAP)
            print(f"✅ Mapped channel post {channel_post_id} → discussion thread {discussion_msg_id}")

# ── post publisher ────────────────────────────────────────────────────────────

def publish_post(photo, caption, slug, parse_mode="Markdown"):
    """
    Publish a photo post to the channel in ONE API call — button is included
    from the start so editMessageReplyMarkup is never called (which would break
    the comment thread linkage).
    """
    button_url = f"{MINI_APP_URL}?startapp={slug}"
    res = tg("sendPhoto", {
        "chat_id":      CHANNEL_ID,
        "photo":        photo,
        "caption":      caption,
        "parse_mode":   parse_mode,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "Оценить дизайн ✦", "url": button_url}
            ]]
        }
    })
    if not res or not res.get("ok"):
        print(f"sendPhoto failed: {res}")
        return None

    msg_id = res["result"]["message_id"]
    SLUG_MAP[slug] = msg_id
    save_slug_map(SLUG_MAP)
    print(f"✅ Published post slug={slug} channel_msg_id={msg_id} button={button_url}")
    return msg_id

# ── admin HTML form ───────────────────────────────────────────────────────────

ADMIN_FORM = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Publish post</title>
<style>
  body{{font-family:sans-serif;max-width:640px;margin:40px auto;padding:0 20px}}
  label{{font-weight:600;display:block;margin-top:14px}}
  input,textarea{{width:100%;padding:8px;margin:4px 0;box-sizing:border-box;font-size:14px}}
  button{{background:#7b2ff7;color:#fff;border:none;padding:10px 28px;cursor:pointer;
          border-radius:6px;margin-top:12px;font-size:15px}}
  pre{{background:#111;color:#0f0;padding:12px;border-radius:6px;overflow:auto}}
  h3{{margin-top:32px}}
</style></head><body>
<h2>Опубликовать пост в канале</h2>
<form method="POST" action="/publish">
  <label>Slug (короткий ID поста, напр: sber, yandex, tinkoff)</label>
  <input name="slug" required placeholder="sber" pattern="[a-z0-9_-]+" title="только латиница, цифры, _ и -">
  <label>Фото (Telegram file_id или https:// URL)</label>
  <input name="photo" required placeholder="AgAC... или https://example.com/photo.jpg">
  <label>Подпись (поддерживает Markdown)</label>
  <textarea name="caption" rows="6" required placeholder="*Сбербанк*\nСайт · Релиз 2025\n\nОписание..."></textarea>
  <button type="submit">Опубликовать</button>
</form>
<h3>SLUG_MAP (slug → channel_msg_id)</h3>
<pre>{slug_map}</pre>
<h3>POST_MAP (channel_msg_id → discussion_thread_id)</h3>
<pre>{post_map}</pre>
</body></html>"""

# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/admin":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = ADMIN_FORM.format(
                slug_map=json.dumps(SLUG_MAP, indent=2),
                post_map=json.dumps(POST_MAP, indent=2),
            )
            self.wfile.write(html.encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status":   "ok",
                "post_map": POST_MAP,
                "slug_map": SLUG_MAP,
            }).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        # ── Telegram webhook ──────────────────────────────────────────────────
        if self.path == "/tg":
            try:
                handle_telegram_update(json.loads(body))
            except Exception as e:
                print("Webhook error:", e)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        # ── Publish post ──────────────────────────────────────────────────────
        if self.path == "/publish":
            ct = self.headers.get("Content-Type", "")
            d  = (json.loads(body) if "application/json" in ct
                  else dict(urllib.parse.parse_qsl(body.decode())))
            photo   = d.get("photo",   "").strip()
            caption = d.get("caption", "").strip()
            slug    = d.get("slug",    "").strip()
            if not photo or not caption or not slug:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False,
                    "error": "photo, caption and slug are required"}).encode())
                return
            msg_id = publish_post(photo, caption, slug)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": bool(msg_id),
                "message_id": msg_id, "slug": slug}).encode())
            return

        # ── Rating from mini-app ──────────────────────────────────────────────
        try:
            data = json.loads(body)
        except:
            self.send_response(400); self.end_headers(); return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        action   = data.get("action", "new")
        post_id  = data.get("postId", "")
        prev_id  = data.get("prevCommentId")
        chat_id  = DISCUSSION_ID if DISCUSSION_ID else CHANNEL_ID

        discussion_thread_id = resolve_discussion_thread(post_id)
        print(f"action={action} post_id={post_id} "
              f"discussion_thread_id={discussion_thread_id} "
              f"SLUG_MAP={SLUG_MAP} POST_MAP={POST_MAP}")

        if action == "delete" and prev_id:
            tg("deleteMessage", {"chat_id": chat_id, "message_id": prev_id})
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        text = format_comment(data)

        if action == "update" and prev_id:
            res = tg("editMessageText", {
                "chat_id":    chat_id,
                "message_id": prev_id,
                "text":       text,
            })
        else:
            payload = {"chat_id": chat_id, "text": text}
            if discussion_thread_id:
                # Both params: message_thread_id for thread routing,
                # reply_to_message_id so the comment visually quotes the post
                payload["message_thread_id"]      = discussion_thread_id
                payload["reply_to_message_id"]    = discussion_thread_id
                payload["allow_sending_without_reply"] = True
            res = tg("sendMessage", payload)

        comment_msg_id = res.get("result", {}).get("message_id") if res else None
        print(f"Result: comment_id={comment_msg_id}")
        self.wfile.write(json.dumps({"ok": True, "commentId": comment_msg_id}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Server running on port {port}")
    server_url = os.environ.get("SERVER_URL", "")
    if server_url:
        result = tg("setWebhook", {
            "url":             f"{server_url}/tg",
            "allowed_updates": ["message", "channel_post"],
        })
        print(f"Webhook set: {result}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
