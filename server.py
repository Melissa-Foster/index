from http.server import HTTPServer, BaseHTTPRequestHandler
import json, urllib.request, urllib.parse, os

BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID  = os.environ.get("CHANNEL_ID", "")
API         = f"https://api.telegram.org/bot{BOT_TOKEN}"

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
    name     = r.get("name", "Аноним")
    username = r.get("username")
    final    = r.get("final", 0)
    comment  = r.get("comment", "").strip()

    mention = f"@{username}" if username else name

    lines = [
        f"👤 {mention}",
        f"",
        f"{'⭐️' * round(final/10)} {final}/100",
        f"",
        f"🎯 Содержание   {score_bar(s.get('content',0))}  {s.get('content','—')}",
        f"🧭 Удобство       {score_bar(s.get('usability',0))}  {s.get('usability','—')}",
        f"✦  Визуал           {score_bar(s.get('visual',0))}  {s.get('visual','—')}",
        f"💡 Идея              {score_bar(s.get('idea',0))}  {s.get('idea','—')}",
    ]

    if comment:
        lines += ["", f"💬 {comment}"]

    return "\n".join(lines)

def get_discussion_message_id(channel_msg_id):
    """Get the corresponding discussion group message id for a channel post"""
    res = tg("forwardMessage", {
        "chat_id": CHANNEL_ID,
        "from_chat_id": CHANNEL_ID,
        "message_id": channel_msg_id,
        "disable_notification": True
    })
    return res

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default logs

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"index rating server ok")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        try:
            data = json.loads(body)
        except:
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        action   = data.get("action", "new")
        post_id  = data.get("postId", "")
        msg_id   = data.get("channelMessageId")  # passed from mini app
        prev_id  = data.get("prevCommentId")      # to edit/delete

        if action == "delete" and prev_id:
            tg("deleteMessage", {
                "chat_id": CHANNEL_ID,
                "message_id": prev_id
            })
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        text = format_comment(data)

        if action == "update" and prev_id:
            # Edit existing comment
            res = tg("editMessageText", {
                "chat_id": CHANNEL_ID,
                "message_id": prev_id,
                "text": text
            })
        else:
            # Post new comment — reply to channel post to appear in comments
            res = tg("sendMessage", {
                "chat_id": CHANNEL_ID,
                "text": text,
                "reply_to_message_id": msg_id
            })

        comment_msg_id = res.get("result", {}).get("message_id") if res else None
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
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
