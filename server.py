from http.server import HTTPServer, BaseHTTPRequestHandler
import json, urllib.request, os

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID    = os.environ.get("CHANNEL_ID", "")
DISCUSSION_ID = os.environ.get("DISCUSSION_ID", "")
API           = f"https://api.telegram.org/bot{BOT_TOKEN}"

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

def parse_startapp(post_id):
    """Extract discussion_msg_id from startapp param like 'post_001_2'"""
    parts = post_id.split("_")
    if len(parts) >= 3:
        try:
            return int(parts[-1])
        except:
            pass
    return None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

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
        prev_id  = data.get("prevCommentId")
        chat_id  = DISCUSSION_ID if DISCUSSION_ID else CHANNEL_ID

        # Extract discussion message id from postId e.g. "post_001_2" → 2
        discussion_msg_id = parse_startapp(post_id)
        print(f"action={action} post_id={post_id} discussion_msg_id={discussion_msg_id}")

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
            payload = {"chat_id": chat_id, "text": text}
            if discussion_msg_id:
                payload["reply_to_message_id"] = discussion_msg_id
            res = tg("sendMessage", payload)

        comment_msg_id = res.get("result", {}).get("message_id") if res else None
        print(f"Result: comment_id={comment_msg_id}, tg_response={res}")
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
