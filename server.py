from http.server import HTTPServer, BaseHTTPRequestHandler
import json, urllib.request, os

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID    = os.environ.get("CHANNEL_ID", "")
DISCUSSION_ID = os.environ.get("DISCUSSION_ID", "")
API           = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAP_FILE      = "/tmp/post_map.json"

# Maps channel post ID → discussion group message ID
# Persisted to disk so it survives server restarts
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

POST_MAP = load_map()

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

def score_bar(val, max_val=10):
    filled = round(val / max_val * 5)
    return "●" * filled + "○" * (5 - filled)

def format_comment(r):
    s = r.get("scores", {})
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

def parse_channel_post_id(post_id):
    """Extract channel post ID from startapp param like 'post_001_15' → 15"""
    parts = post_id.split("_")
    if len(parts) >= 3:
        try:
            return int(parts[-1])
        except:
            pass
    return None

def handle_telegram_update(update):
    """
    Called when Telegram sends an update to our webhook.
    Detects auto-forwarded channel posts in the discussion group
    and stores the mapping: channel_post_id → discussion_group_message_id.
    """
    print(f"TG update received: {json.dumps(update)[:500]}")

    msg = update.get("message")
    if not msg:
        return

    # Detect automatic forwards from channel to discussion group
    # is_automatic_forward is True when Telegram auto-forwards a channel post to its linked group
    if msg.get("is_automatic_forward"):
        channel_post_id = msg.get("forward_from_message_id")
        discussion_msg_id = msg.get("message_id")
        if channel_post_id and discussion_msg_id:
            POST_MAP[channel_post_id] = discussion_msg_id
            save_map(POST_MAP)
            print(f"✅ Mapped channel post {channel_post_id} → discussion msg {discussion_msg_id}")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "post_map": POST_MAP}).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            data = json.loads(body)
        except:
            self.send_response(400)
            self.end_headers()
            return

        # Telegram webhook updates come to /tg
        if self.path == "/tg":
            handle_telegram_update(data)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        action          = data.get("action", "new")
        post_id         = data.get("postId", "")
        prev_id         = data.get("prevCommentId")
        chat_id         = DISCUSSION_ID if DISCUSSION_ID else CHANNEL_ID

        # startapp param contains channel post ID (e.g. "post_001_15" → 15)
        channel_post_id = parse_channel_post_id(post_id)

        # Look up the discussion group thread ID automatically
        discussion_thread_id = POST_MAP.get(channel_post_id) if channel_post_id else None

        print(f"action={action} post_id={post_id} channel_post_id={channel_post_id} discussion_thread_id={discussion_thread_id} POST_MAP={POST_MAP}")

        if action == "delete" and prev_id:
            tg("deleteMessage", {"chat_id": chat_id, "message_id": prev_id})
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        text = format_comment(data)

        if action == "update" and prev_id:
            res = tg("editMessageText", {
                "chat_id": chat_id,
                "message_id": prev_id,
                "text": text
            })
        else:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "allow_sending_without_reply": True
            }
            if discussion_thread_id:
                # reply_to_message_id links the comment to the channel post thread
                payload["reply_to_message_id"] = discussion_thread_id
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
    # Register webhook with Telegram so we receive group updates
    server_url = os.environ.get("SERVER_URL", "")
    if server_url:
        result = tg("setWebhook", {
            "url": f"{server_url}/tg",
            "allowed_updates": ["message", "channel_post"]
        })
        print(f"Webhook set: {result}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
