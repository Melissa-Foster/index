from http.server import HTTPServer, BaseHTTPRequestHandler
import json, urllib.request, urllib.parse, os, re, threading

def parse_multipart(body: bytes, content_type: str):
    """Parse multipart/form-data without the removed cgi module (Python 3.13+)."""
    m = re.search(r'boundary=([^\s;]+)', content_type)
    if not m:
        return {}, {}
    boundary = m.group(1).strip('"').encode()
    fields, files = {}, {}
    for part in body.split(b'--' + boundary):
        if part in (b'', b'--\r\n', b'--') or part.startswith(b'--'):
            continue
        if part.startswith(b'\r\n'):
            part = part[2:]
        if b'\r\n\r\n' not in part:
            continue
        hdr_raw, content = part.split(b'\r\n\r\n', 1)
        if content.endswith(b'\r\n'):
            content = content[:-2]
        hdr = hdr_raw.decode('utf-8', errors='replace')
        nm  = re.search(r'name="([^"]*)"',     hdr)
        fnm = re.search(r'filename="([^"]*)"', hdr)
        if not nm:
            continue
        name = nm.group(1)
        if fnm and fnm.group(1):
            files[name]  = content
        else:
            fields[name] = content.decode('utf-8', errors='replace')
    return fields, files

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID    = os.environ.get("CHANNEL_ID", "")
DISCUSSION_ID = os.environ.get("DISCUSSION_ID", "")
MINI_APP_URL  = os.environ.get("MINI_APP_URL", "https://t.me/designindexxx_bot/rate")
API           = f"https://api.telegram.org/bot{BOT_TOKEN}"

DATA_DIR    = "/data"
MAP_FILE    = f"{DATA_DIR}/post_map.json"
SLUG_FILE   = f"{DATA_DIR}/slug_map.json"
AVATARS_DIR = f"{DATA_DIR}/avatars"
AVATARS_IDX = f"{AVATARS_DIR}/index.json"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(AVATARS_DIR, exist_ok=True)

def load_avatars():
    if os.path.exists(AVATARS_IDX):
        try:
            with open(AVATARS_IDX) as f:
                return json.load(f)
        except:
            pass
    return []

def save_avatars(lst):
    with open(AVATARS_IDX, "w") as f:
        json.dump(lst, f, ensure_ascii=False)

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
SLUG_MAP = load_slug_map()  # {slug: {channel_msg_id, button_msg_id, button_text, votes, name, subtitle, photo_url, ...}}

# ── startup diagnostics ───────────────────────────────────────────────────────
import stat
_is_mount = False
try:
    _data_stat = os.stat(DATA_DIR)
    _root_stat = os.stat("/")
    _is_mount  = _data_stat.st_dev != _root_stat.st_dev
except Exception as _e:
    print(f"[DIAG] stat error: {_e}")
print(f"[DIAG] DATA_DIR={DATA_DIR} is_mount={_is_mount} "
      f"files={os.listdir(DATA_DIR)} "
      f"POST_MAP_size={len(POST_MAP)} SLUG_MAP_size={len(SLUG_MAP)}")

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
        try:
            print("TG error body:", e.read().decode())
        except Exception:
            pass
        return None

def extract_video_thumbnail(video_bytes: bytes):
    """Extract a frame from video at 1s using ffmpeg, return JPEG bytes."""
    import subprocess, tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
            vf.write(video_bytes)
            vpath = vf.name
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            tpath = tf.name
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "1", "-i", vpath,
             "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "2", tpath],
            capture_output=True, timeout=30
        )
        if os.path.exists(tpath) and os.path.getsize(tpath) > 0:
            with open(tpath, "rb") as f:
                return f.read()
    except Exception as e:
        print(f"thumbnail extraction failed: {e}")
    finally:
        for p in [vpath, tpath]:
            try: os.unlink(p)
            except: pass
    return None

def is_video(data: bytes) -> bool:
    """Detect video by magic bytes (MP4/MOV/AVI)."""
    if len(data) > 12 and data[4:8] == b"ftyp":
        return True
    if data[:4] == b"RIFF" and len(data) > 11 and data[8:11] == b"AVI":
        return True
    return False

def tg_file(method, fields, file_field, file_bytes, filename="file", content_type="image/jpeg",
            thumb_bytes=None):
    """Send multipart/form-data request to Telegram (for file uploads)."""
    boundary = b"----TGFileBoundary"
    parts = bytearray()
    for k, v in fields.items():
        parts += b"--" + boundary + b"\r\n"
        parts += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        parts += str(v).encode() + b"\r\n"
    parts += b"--" + boundary + b"\r\n"
    parts += f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    parts += f"Content-Type: {content_type}\r\n\r\n".encode()
    parts += file_bytes + b"\r\n"
    if thumb_bytes:
        parts += b"--" + boundary + b"\r\n"
        parts += b'Content-Disposition: form-data; name="thumbnail"; filename="thumb.jpg"\r\n'
        parts += b"Content-Type: image/jpeg\r\n\r\n"
        parts += thumb_bytes + b"\r\n"
    parts += b"--" + boundary + b"--\r\n"
    req = urllib.request.Request(
        f"{API}/{method}",
        data=bytes(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:
        print("TG file error:", e)
        try:
            print("TG file error body:", e.read().decode())
        except Exception:
            pass
        return None

# ── comment formatting ────────────────────────────────────────────────────────

def score_bar(val, max_val=5):
    filled = round(val / max_val * 5)
    return "●" * filled + "○" * (5 - filled)

def _esc(text):
    """Escape HTML special chars in user-provided text."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def format_comment(r):
    s        = r.get("scores", {})
    username = r.get("username")
    name     = r.get("name", "Аноним")
    final    = r.get("final", 0)
    comment  = r.get("comment", "").strip()
    mention  = f"@{_esc(username)}" if username else _esc(name)

    def row(label, key):
        val = s.get(key, 0)
        num = f"{val:>2}" if isinstance(val, int) else " —"
        return f"{label}  {score_bar(val)}  {num}"

    # Labels padded to equal width (8 chars) for monospace alignment
    # <pre> renders in monospace in Telegram HTML mode
    criteria = "\n".join([
        row("Смысл   ", "content"),
        row("Удобство", "usability"),
        row("Визуал  ", "visual"),
        row("Идея    ", "idea"),
    ])
    lines = [
        f"👤 {mention}", "",
        f"⭐ {final}/17", "",
        f"<code>{criteria}</code>",
    ]
    if comment:
        lines += ["", f"💬 {_esc(comment)}"]
    return "\n".join(lines)

# ── average score ─────────────────────────────────────────────────────────────

def _vote_word(n):
    if n % 100 in range(11, 20):
        return "голосов"
    r = n % 10
    if r == 1:   return "голос"
    if r in (2, 3, 4): return "голоса"
    return "голосов"

def update_average(slug):
    """Edit the button message in the channel to show the current average score."""
    entry = SLUG_MAP.get(slug)
    if not isinstance(entry, dict):
        return
    button_msg_id = entry.get("button_msg_id")
    if not button_msg_id:
        return

    button_text = entry.get("button_text", "Оценить дизайн ✦")
    votes       = entry.get("votes", {})
    button_url  = f"{MINI_APP_URL}?startapp={slug}"

    if votes:
        avg   = sum(votes.values()) / len(votes)
        count = len(votes)
        # Per-criterion averages
        scores_by_user = entry.get("scores_by_user", {})
        crit_line = ""
        if scores_by_user:
            keys = [("content", "Смысл"), ("usability", "Удобство"), ("visual", "Визуал"), ("idea", "Идея")]
            parts = []
            for key, label in keys:
                vals = [s[key] for s in scores_by_user.values() if key in s]
                if vals:
                    parts.append(f"{label} {round(sum(vals)/len(vals))}")
            if parts:
                crit_line = " | " + " · ".join(parts)
        first_line = f"⭐ <b>{round(avg)}/17</b>{crit_line}"
        text = f"{first_line}\n{count} {_vote_word(count)}"
    else:
        text = "0 голосов"

    res = tg("editMessageText", {
        "chat_id":      CHANNEL_ID,
        "message_id":   button_msg_id,
        "text":         text,
        "parse_mode":   "HTML",
        "reply_markup": {
            "inline_keyboard": [[{"text": button_text, "url": button_url}]]
        }
    })
    if not res or not res.get("ok"):
        print(f"[WARN] editMessageText failed for slug={slug} button_msg_id={button_msg_id}: {res}")

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
      - slug  (e.g. 'sber')         → SLUG_MAP[slug].channel_msg_id → POST_MAP
      - legacy (e.g. 'post_001_15') → POST_MAP[15]
    """
    entry = SLUG_MAP.get(post_id)
    if entry:
        channel_post_id = entry.get("channel_msg_id") if isinstance(entry, dict) else entry
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

def publish_post(photo, caption, slug, button_text="Оценить дизайн ✦",
                 parse_mode="Markdown", name="", subtitle="", photo_bytes=None, thumb_bytes=None):
    """
    1. Publish photo (no button) — comment section stays visible.
    2. Send rating button as a separate channel message.
    SLUG_MAP[slug] stores channel_msg_id, button_msg_id, button_text, votes, name, subtitle, photo_file_id.
    """
    # Step 1: publish media or text with no inline keyboard
    if photo_bytes:
        video = is_video(photo_bytes)
        tg_method  = "sendVideo" if video else "sendPhoto"
        tg_field   = "video"     if video else "photo"
        tg_ctype   = "video/mp4" if video else "image/jpeg"
        tg_fname   = "video.mp4" if video else "photo.jpg"
        res = tg_file(tg_method, {
            "chat_id":    CHANNEL_ID,
            "caption":    caption,
            "parse_mode": parse_mode,
        }, tg_field, photo_bytes, filename=tg_fname, content_type=tg_ctype,
            thumb_bytes=thumb_bytes if video else None)
    else:
        res = tg("sendMessage", {
            "chat_id":    CHANNEL_ID,
            "text":       caption,
            "parse_mode": parse_mode,
        })
    if not res or not res.get("ok"):
        print(f"send media failed: {res}")
        return None

    photo_msg_id = res["result"]["message_id"]

    # Extract file_id for the proxy endpoint
    photos    = res["result"].get("photo", [])
    video_obj = res["result"].get("video", {})
    photo_file_id = (photos[-1]["file_id"] if photos
                     else video_obj.get("thumbnail", {}).get("file_id", "")
                     or video_obj.get("file_id", ""))

    # Step 2: send button message
    button_url = f"{MINI_APP_URL}?startapp={slug}"
    res2 = tg("sendMessage", {
        "chat_id": CHANNEL_ID,
        "text":    "0 голосов",
        "reply_markup": {
            "inline_keyboard": [[{"text": button_text, "url": button_url}]]
        }
    })
    button_msg_id = res2["result"]["message_id"] if res2 and res2.get("ok") else None

    SLUG_MAP[slug] = {
        "channel_msg_id": photo_msg_id,
        "button_msg_id":  button_msg_id,
        "button_text":    button_text,
        "name":           name,
        "subtitle":       subtitle,
        "photo_file_id":  photo_file_id,   # auto-captured Telegram file_id
        "votes":          {},
        "comment_ids":    {},  # {username: comment_msg_id}
    }
    save_slug_map(SLUG_MAP)
    print(f"✅ Published post slug={slug} channel_msg_id={photo_msg_id} "
          f"button_msg_id={button_msg_id} button_url={button_url}")
    return photo_msg_id

# ── admin HTML form ───────────────────────────────────────────────────────────

ADMIN_FORM = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Publish post</title>
<style>
  body{font-family:sans-serif;max-width:640px;margin:40px auto;padding:0 20px}
  label{font-weight:600;display:block;margin-top:14px}
  input,textarea{width:100%;padding:8px;margin:4px 0;box-sizing:border-box;font-size:14px}
  button{background:#7b2ff7;color:#fff;border:none;padding:10px 28px;cursor:pointer;
          border-radius:6px;margin-top:12px;font-size:15px}
  small{color:#888;font-size:12px}
  pre{background:#111;color:#0f0;padding:12px;border-radius:6px;overflow:auto;font-size:12px}
  #modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:100;align-items:center;justify-content:center}
  #modal-overlay.open{display:flex}
  #modal-box{background:#fff;border-radius:10px;padding:28px 32px;max-width:340px;width:90%;text-align:center}
  #modal-box p{margin:0 0 20px;font-size:15px}
  #modal-box .btns{display:flex;gap:12px;justify-content:center}
  #modal-box .btns button{margin-top:0}
  h3{margin-top:32px}
  .drop-zone{border:2px dashed #ccc;border-radius:8px;padding:16px;margin:4px 0;cursor:pointer;text-align:center;background:#fafafa;transition:border-color .2s,background .2s;position:relative}
  .drop-zone:hover,.drop-zone.active{border-color:#7b2ff7;background:#f3eeff}
  .drop-zone input[type=file]{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
  .drop-zone .dz-hint{color:#999;font-size:13px;font-weight:400;pointer-events:none}
  .drop-zone .dz-preview{display:none;margin-top:8px;pointer-events:none}
  .drop-zone .dz-preview img{width:100%;height:160px;border-radius:6px;object-fit:cover;display:block}
  .drop-zone .dz-name{font-size:12px;color:#555;margin-top:4px}
  .dz-clear{display:none;margin-top:6px;font-size:12px;color:#c00;background:none;border:none;cursor:pointer;padding:0;pointer-events:all}
  .avatar-picker-toggle{font-size:13px;color:#7b2ff7;background:none;border:none;cursor:pointer;padding:0;margin-top:4px;display:block}
  .avatar-grid{display:none;flex-wrap:wrap;gap:10px;margin-top:10px}
  .avatar-grid.open{display:flex}
  .avatar-item{display:flex;flex-direction:column;align-items:center;gap:4px;cursor:pointer;width:72px}
  .avatar-item img{width:56px;height:56px;border-radius:50%;object-fit:cover;border:2px solid transparent;transition:border-color .15s}
  .avatar-item:hover img,.avatar-item.selected img{border-color:#7b2ff7}
  .avatar-item span{font-size:11px;text-align:center;color:#333;line-height:1.2}
  .avatar-item .del-av{font-size:10px;color:#c00;background:none;border:none;cursor:pointer;padding:0;display:none}
  .avatar-item:hover .del-av{display:block}
</style></head><body>
<h2>Опубликовать пост в канале</h2>
<form method="POST" action="/publish" enctype="multipart/form-data">
  <h3 style="margin-top:16px">Пост в канале</h3>
  <label>Фото или видео поста (jpg/png/mp4/mov) — необязательно</label>
  <div class="drop-zone" id="dz-post">
    <input name="post_photo" type="file" accept="image/*,video/*">
    <div class="dz-hint">Нажми, перетащи файл или вставь из буфера <b>⌘V</b></div>
    <div class="dz-preview"><img id="dz-post-img" src=""><div class="dz-name" id="dz-post-name"></div></div>
  </div>
  <button type="button" class="dz-clear" id="dz-post-clear">✕ Удалить файл</button>
  <label>Подпись (Markdown: *жирный*, _курсив_, [текст](https://url))</label>
  <textarea name="caption" rows="6" required placeholder="*Сбербанк*\nСайт · Релиз 2025\n\nОписание...\n\n[Открыть сайт](https://sber.ru)"></textarea>
  <label>Текст кнопки оценки</label>
  <input name="button_text" required placeholder="Оценить дизайн ✦" value="Оценить дизайн ✦">
  <h3>Мини-апп</h3>
  <label>Slug (короткий ID поста, напр: sber, yandex, tinkoff)</label>
  <input name="slug" required placeholder="sber" pattern="[a-z0-9_-]+" title="только латиница, цифры, _ и -">
  <label>Название (отображается в мини-апп)</label>
  <input name="name" required placeholder="Сбербанк">
  <label>Подзаголовок (тип + год, напр: Сайт, релиз 2026)</label>
  <input name="subtitle" required placeholder="Сайт, релиз 2026">
  <label>Фото для мини-апп (загрузить файл — jpg/png)</label>
  <div class="drop-zone" id="dz-mini">
    <input name="photo_file" type="file" accept="image/*">
    <div class="dz-hint">Нажми, перетащи файл или вставь из буфера <b>⌘V</b></div>
    <div class="dz-preview"><img id="dz-mini-img" src=""><div class="dz-name" id="dz-mini-name"></div></div>
  </div>
  <button type="button" class="dz-clear" id="dz-mini-clear">✕ Удалить файл</button>
  <div class="avatar-grid open" id="avatar-grid"></div>
  <button type="submit" id="btn">Опубликовать</button>
  <p id="status" style="color:#0a0;font-weight:600;display:none">✅ Публикация запущена — пост появится в канале через несколько секунд</p>
</form>
<script>
// Drop-zone: preview + paste + drag
var zones = [];
var applyFile;
document.addEventListener("DOMContentLoaded", function() {
(function() {
  zones = [
    {zone: document.getElementById('dz-post'), inp: document.querySelector('[name="post_photo"]'),   img: document.getElementById('dz-post-img'), name: document.getElementById('dz-post-name'), clear: document.getElementById('dz-post-clear')},
    {zone: document.getElementById('dz-mini'), inp: document.querySelector('[name="photo_file"]'),   img: document.getElementById('dz-mini-img'), name: document.getElementById('dz-mini-name'), clear: document.getElementById('dz-mini-clear')},
    {zone: document.getElementById('dz-av'),   inp: document.querySelector('[name="avatar_photo"]'), img: document.getElementById('dz-av-img'),   name: document.getElementById('dz-av-name'),   clear: document.getElementById('dz-av-clear')}
  ];
  var lastZone = zones[0];

  function showPreview(z, file) {
    z.name.textContent = file.name;
    if (file.type.indexOf('image') === 0) {
      var reader = new FileReader();
      reader.onload = function(e) { z.img.src = e.target.result; z.img.style.display = 'block'; };
      reader.readAsDataURL(file);
    } else {
      z.img.style.display = 'none';
    }
    z.zone.querySelector('.dz-preview').style.display = 'block';
    z.zone.querySelector('.dz-hint').style.display = 'none';
    z.clear.style.display = 'block';
  }

  function clearZone(z) {
    z.inp.value = '';
    z.img.src = ''; z.img.style.display = 'none';
    z.name.textContent = '';
    z.zone.querySelector('.dz-preview').style.display = 'none';
    z.zone.querySelector('.dz-hint').style.display = '';
    z.clear.style.display = 'none';
  }

  applyFile = function(z, file) {
    var dt = new DataTransfer();
    dt.items.add(file);
    z.inp.files = dt.files;
    showPreview(z, file);
  }

  zones.forEach(function(z) {
    z.zone.addEventListener('click', function() { lastZone = z; });
    z.clear.addEventListener('click', function() { clearZone(z); });
    z.inp.addEventListener('change', function() {
      if (z.inp.files[0]) showPreview(z, z.inp.files[0]);
    });
    z.zone.addEventListener('dragover', function(e) { e.preventDefault(); z.zone.classList.add('active'); });
    z.zone.addEventListener('dragleave', function() { z.zone.classList.remove('active'); });
    z.zone.addEventListener('drop', function(e) {
      e.preventDefault(); z.zone.classList.remove('active');
      var file = e.dataTransfer.files[0];
      if (file) applyFile(z, file);
    });
  });

  document.addEventListener('paste', function(e) {
    var items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (var i = 0; i < items.length; i++) {
      if (items[i].type.indexOf('image') === -1) continue;
      var file = items[i].getAsFile();
      if (!file) { continue; }
      applyFile(lastZone, file);
      e.preventDefault();
      break;
    }
  });
})();
}); // DOMContentLoaded
document.querySelector("form").addEventListener("submit", function(e) {
  e.preventDefault();
  var btn = document.getElementById("btn");
  var status = document.getElementById("status");
  btn.disabled = true; btn.textContent = "Публикуется...";
  fetch("/publish", {method:"POST", body: new FormData(this)})
    .then(function(r){ return r.json(); })
    .then(function(){ status.style.display="block"; btn.textContent="Опубликовать"; btn.disabled=false; })
    .catch(function(){ btn.textContent="Ошибка, попробуй ещё раз"; btn.disabled=false; });
});
</script>
<hr style="margin-top:48px">
<h2>Починить кнопку поста</h2>
<p style="color:#666;font-size:13px">Если кнопка с оценкой была удалена или не обновляется — создаст новую и обновит ID в базе.</p>
<form id="repair-form">
  <label>Slug поста</label>
  <input id="repair-slug" required placeholder="sber">
  <label>ID поста (необязательно — channel_msg_id, если комментарии не привязаны)</label>
  <input id="repair-cmid" placeholder="100" type="number">
  <label>ID кнопки (необязательно — если кнопка сломана, укажи правильный message_id)</label>
  <input id="repair-bmid" placeholder="101" type="number">
  <button type="submit" id="repair-btn">Починить</button>
  <button type="button" id="reset-btn" style="background:#c00;margin-left:8px">Сбросить голоса</button>
  <p id="repair-status" style="font-weight:600;display:none"></p>
</form>
<script>
document.getElementById("repair-form").addEventListener("submit", function(e) {
  e.preventDefault();
  var slug = document.getElementById("repair-slug").value.trim();
  var btn = document.getElementById("repair-btn");
  var status = document.getElementById("repair-status");
  btn.disabled = true; btn.textContent = "Отправляю...";
  status.style.display = "none";
  var bmid = document.getElementById("repair-bmid").value.trim();
  var cmid = document.getElementById("repair-cmid").value.trim();
  var payload = {slug: slug};
  if (bmid) payload.button_msg_id = bmid;
  if (cmid) payload.channel_msg_id = cmid;
  fetch("/repair", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d.ok) {
        status.style.color = "#0a0";
        status.textContent = "✅ Починено! Новый button_msg_id: " + d.new_button_msg_id;
      } else {
        status.style.color = "#c00";
        status.textContent = "❌ " + (d.error || JSON.stringify(d));
      }
      status.style.display = "block";
      btn.textContent = "Починить"; btn.disabled = false;
    })
    .catch(function(){ status.style.color="#c00"; status.textContent="❌ Ошибка сети"; status.style.display="block"; btn.textContent="Починить"; btn.disabled=false; });
});
document.getElementById("reset-btn").addEventListener("click", function() {
  var slug = document.getElementById("repair-slug").value.trim();
  var status = document.getElementById("repair-status");
  if (!slug) { status.style.color="#c00"; status.textContent="❌ Введи slug"; status.style.display="block"; return; }
  document.getElementById("modal-msg").textContent = "Сбросить все голоса для «" + slug + "»?";
  document.getElementById("modal-overlay").classList.add("open");
  document.getElementById("modal-confirm").onclick = function() {
    document.getElementById("modal-overlay").classList.remove("open");
    fetch("/reset-votes", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({slug: slug})})
      .then(function(r){ return r.json(); })
      .then(function(d){
        status.style.color = d.ok ? "#0a0" : "#c00";
        status.textContent = d.ok ? "✅ Голоса сброшены" : "❌ " + (d.error || JSON.stringify(d));
        status.style.display = "block";
      })
      .catch(function(){ status.style.color="#c00"; status.textContent="❌ Ошибка сети"; status.style.display="block"; });
  };
  document.getElementById("modal-cancel").onclick = function() {
    document.getElementById("modal-overlay").classList.remove("open");
  };
});
</script>
<hr style="margin-top:48px">
<h2>Аватары</h2>
<p style="color:#666;font-size:13px">Фотографии участников для мини-апп. После загрузки появятся в пикере выше.</p>
<form id="av-upload-form" enctype="multipart/form-data">
  <label>Имя</label>
  <input name="avatar_name" id="av-name" placeholder="Мелисса" required>
  <label>Фото</label>
  <div class="drop-zone" id="dz-av">
    <input name="avatar_photo" type="file" accept="image/*">
    <div class="dz-hint">Нажми, перетащи или вставь ⌘V</div>
    <div class="dz-preview"><img id="dz-av-img" src=""><div class="dz-name" id="dz-av-name"></div></div>
  </div>
  <button type="button" class="dz-clear" id="dz-av-clear">✕ Удалить файл</button>
  <button type="submit" id="av-btn" style="margin-top:12px">Добавить аватар</button>
  <p id="av-status" style="font-size:13px;display:none"></p>
</form>
<div id="av-manage-grid" style="display:flex;flex-wrap:wrap;gap:10px;margin-top:16px"></div>
<script>
// Avatar upload form
document.getElementById("av-upload-form").addEventListener("submit", function(e) {
  e.preventDefault();
  var btn = document.getElementById("av-btn");
  var status = document.getElementById("av-status");
  btn.disabled = true; btn.textContent = "Загружается...";
  fetch("/upload-avatar", {method:"POST", body: new FormData(this)})
    .then(function(r){ return r.json(); })
    .then(function(d){
      btn.disabled = false; btn.textContent = "Добавить аватар";
      status.style.display = "block";
      status.style.color = d.ok ? "#0a0" : "#c00";
      status.textContent = d.ok ? "✅ Аватар добавлен" : "❌ " + (d.error || "ошибка");
      if (d.ok) { loadAvatarGrid(); loadAvatarPicker(); }
    })
    .catch(function(){ btn.disabled=false; btn.textContent="Добавить аватар"; status.style.display="block"; status.style.color="#c00"; status.textContent="❌ Ошибка сети"; });
});

// Avatar picker (in publish form)

function loadAvatarPicker() {
  fetch("/list-avatars").then(function(r){ return r.json(); }).then(function(list) {
    var grid = document.getElementById("avatar-grid");
    grid.innerHTML = "";
    list.forEach(function(av) {
      var item = document.createElement("div");
      item.className = "avatar-item";
      item.innerHTML = '<img src="/avatar/' + av.file + '"><span>' + av.name + '</span>';
      item.addEventListener("click", function() {
        grid.querySelectorAll(".avatar-item").forEach(function(i){ i.classList.remove("selected"); });
        item.classList.add("selected");
        fetch("/avatar/" + av.file).then(function(r){ return r.blob(); }).then(function(blob) {
          var file = new File([blob], av.file, {type: "image/jpeg"});
          var zMini = zones.find(function(z){ return z.inp.name === "photo_file"; });
          if (zMini) applyFile(zMini, file);
          var nameInput = document.querySelector('[name="name"]');
          if (nameInput && !nameInput.value) nameInput.value = av.name;
        });
      });
      grid.appendChild(item);
    });
  });
}

// Avatar management grid (bottom section)
function loadAvatarGrid() {
  fetch("/list-avatars").then(function(r){ return r.json(); }).then(function(list) {
    var grid = document.getElementById("av-manage-grid");
    grid.innerHTML = "";
    list.forEach(function(av) {
      var item = document.createElement("div");
      item.className = "avatar-item";
      item.innerHTML = '<img src="/avatar/' + av.file + '"><span>' + av.name + '</span><button class="del-av">удалить</button>';
      item.querySelector(".del-av").addEventListener("click", function() {
        if (!confirm("Удалить " + av.name + "?")) return;
        fetch("/delete-avatar", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({file: av.file})})
          .then(function(){ loadAvatarGrid(); loadAvatarPicker(); });
      });
      grid.appendChild(item);
    });
  });
}

loadAvatarPicker();
loadAvatarGrid();
</script>
<div id="modal-overlay">
  <div id="modal-box">
    <p id="modal-msg"></p>
    <div class="btns">
      <button id="modal-cancel" style="background:#888">Отмена</button>
      <button id="modal-confirm" style="background:#c00">Сбросить</button>
    </div>
  </div>
</div>
</body></html>"""

# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        # ── GET /photo/{slug} — serve thumbnail for mini-app ─────────────────
        if self.path.startswith("/photo/"):
            slug  = self.path[7:].split("?")[0]

            # 1. Locally uploaded file (from admin form)
            local_path = f"{DATA_DIR}/photos/{slug}"
            if os.path.exists(local_path):
                with open(local_path, "rb") as pf:
                    img_bytes = pf.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(img_bytes)))
                self.end_headers()
                self.wfile.write(img_bytes)
                return

            entry = SLUG_MAP.get(slug)
            if not entry or not isinstance(entry, dict):
                self.send_response(404); self.end_headers(); return

            # 2. Resolve Telegram file_id → file_path → proxy image bytes
            file_id = entry.get("photo_file_id", "")
            if not file_id:
                self.send_response(404); self.end_headers(); return
            file_res = tg("getFile", {"file_id": file_id})
            if not file_res or not file_res.get("ok"):
                self.send_response(404); self.end_headers(); return
            file_path = file_res["result"]["file_path"]
            tg_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            try:
                with urllib.request.urlopen(tg_url) as img:
                    img_bytes   = img.read()
                    content_type = img.headers.get("Content-Type", "image/jpeg")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(img_bytes)))
                self.end_headers()
                self.wfile.write(img_bytes)
            except Exception as e:
                print(f"Photo proxy error: {e}")
                self.send_response(502); self.end_headers()
            return

        # ── GET /avatar/<file> — serve avatar image ───────────────────────────
        if self.path.startswith("/avatar/"):
            fname = os.path.basename(urllib.parse.unquote(self.path[8:].split("?")[0]))
            fpath = os.path.join(AVATARS_DIR, fname)
            if os.path.exists(fpath):
                with open(fpath, "rb") as af:
                    data = af.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404); self.end_headers()
            return

        # ── GET /stats — aggregated per-person stats ──────────────────────────────
        if self.path == "/stats":
            stats = {}
            for slug, entry in SLUG_MAP.items():
                if not isinstance(entry, dict):
                    continue
                person = entry.get("name", "").strip()
                if not person:
                    continue
                scores_by_user = entry.get("scores_by_user", {})
                if not scores_by_user:
                    continue
                if person not in stats:
                    stats[person] = {"person": person, "works": [], "votes_total": 0}
                # collect per-work avg
                work_scores = list(scores_by_user.values())
                work_votes = entry.get("votes", {})
                criteria = ["content", "usability", "visual", "idea"]
                work_avg_by_crit = {}
                for c in criteria:
                    vals = [s.get(c, 0) for s in work_scores if s.get(c)]
                    work_avg_by_crit[c] = round(sum(vals) / len(vals), 2) if vals else 0
                # final scores from votes dict
                vote_vals = [v for v in work_votes.values() if v]
                work_avg_total = round(sum(vote_vals) / len(vote_vals), 1) if vote_vals else 0
                stats[person]["works"].append({
                    "slug": slug,
                    "subtitle": entry.get("subtitle", ""),
                    "avg_total": work_avg_total,
                    "avg_by_crit": work_avg_by_crit,
                    "votes_count": len(vote_vals),
                })
                stats[person]["votes_total"] += len(vote_vals)
            # compute overall averages per person
            result = []
            criteria = ["content", "usability", "visual", "idea"]
            for person, data in stats.items():
                works = data["works"]
                if not works:
                    continue
                overall_total = round(sum(w["avg_total"] for w in works) / len(works), 1)
                overall_by_crit = {}
                for c in criteria:
                    vals = [w["avg_by_crit"][c] for w in works if w["avg_by_crit"].get(c)]
                    overall_by_crit[c] = round(sum(vals) / len(vals), 1) if vals else 0
                result.append({
                    "person": person,
                    "avg_total": overall_total,
                    "avg_by_crit": overall_by_crit,
                    "works_count": len(works),
                    "votes_total": data["votes_total"],
                    "works": works,
                })
            result.sort(key=lambda x: x["avg_total"], reverse=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            return

        # ── GET /list-avatars — return avatar list JSON ────────────────────────
        if self.path == "/list-avatars":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(load_avatars(), ensure_ascii=False).encode())
            return

        # ── GET /post/{slug} — mini-app fetches post metadata ─────────────────
        if self.path.startswith("/post/"):
            slug = self.path[6:].split("?")[0]
            entry = SLUG_MAP.get(slug)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if isinstance(entry, dict):
                self.wfile.write(json.dumps({
                    "ok":        True,
                    "name":      entry.get("name", ""),
                    "subtitle":  entry.get("subtitle", ""),
                    "photo_url": entry.get("photo_url", ""),
                }, ensure_ascii=False).encode())
            else:
                self.wfile.write(json.dumps({"ok": False}).encode())
            return

        if self.path == "/admin":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            slug_json = json.dumps(SLUG_MAP, indent=2, ensure_ascii=False)
            post_json = json.dumps(POST_MAP, indent=2)
            status = (
                f'<h3>SLUG_MAP <button onclick="navigator.clipboard.writeText(document.getElementById(\'slug-pre\').textContent)" style="font-size:12px;padding:2px 8px;cursor:pointer">📋</button></h3>'
                f'<pre id="slug-pre" style="max-height:200px;overflow:auto">{slug_json}</pre>'
                f'<h3>POST_MAP <button onclick="navigator.clipboard.writeText(document.getElementById(\'post-pre\').textContent)" style="font-size:12px;padding:2px 8px;cursor:pointer">📋</button></h3>'
                f'<pre id="post-pre" style="max-height:200px;overflow:auto">{post_json}</pre>'
            )
            html = ADMIN_FORM.replace("</body></html>", status + "</body></html>")
            self.wfile.write(html.encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status":   "ok",
                "post_map": POST_MAP,
                "slug_map": SLUG_MAP,
            }, ensure_ascii=False).encode())

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
            photo_file_data = None
            post_photo_data = None
            if "multipart/form-data" in ct:
                fields, files = parse_multipart(body, ct)
                def fval(k): return fields.get(k, "").strip()
                caption     = fval("caption")
                slug        = fval("slug")
                button_text = fval("button_text") or "Оценить дизайн ✦"
                name        = fval("name")
                subtitle    = fval("subtitle")
                if "photo_file" in files:
                    photo_file_data = files["photo_file"]
                if "post_photo" in files:
                    post_photo_data = files["post_photo"]
            elif "application/json" in ct:
                d = json.loads(body)
                caption     = d.get("caption",     "").strip()
                slug        = d.get("slug",        "").strip()
                button_text = d.get("button_text", "Оценить дизайн ✦").strip() or "Оценить дизайн ✦"
                name        = d.get("name",        "").strip()
                subtitle    = d.get("subtitle",    "").strip()
            else:
                d = dict(urllib.parse.parse_qsl(body.decode()))
                caption     = d.get("caption",     "").strip()
                slug        = d.get("slug",        "").strip()
                button_text = d.get("button_text", "Оценить дизайн ✦").strip() or "Оценить дизайн ✦"
                name        = d.get("name",        "").strip()
                subtitle    = d.get("subtitle",    "").strip()
            if not caption or not slug:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False,
                    "error": "caption and slug are required"}).encode())
                return
            # Save uploaded thumbnail to disk for /photo/{slug}
            if photo_file_data and slug:
                os.makedirs(f"{DATA_DIR}/photos", exist_ok=True)
                with open(f"{DATA_DIR}/photos/{slug}", "wb") as pf:
                    pf.write(photo_file_data)

            # Publish in background so the form doesn't hang on large files
            def do_publish():
                publish_post(None, caption, slug, button_text,
                             name=name, subtitle=subtitle, photo_bytes=post_photo_data)
            threading.Thread(target=do_publish, daemon=True).start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "slug": slug,
                "message": "публикация запущена, пост появится через несколько секунд"}).encode())
            return

        # ── Reset votes for a slug ───────────────────────────────────────────
        if self.path == "/reset-votes":
            try:
                d = json.loads(body)
            except Exception:
                d = dict(urllib.parse.parse_qsl(body.decode()))
            slug = d.get("slug", "").strip()
            entry = SLUG_MAP.get(slug) if isinstance(SLUG_MAP.get(slug), dict) else None
            if not entry:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": f"slug '{slug}' not found"}).encode())
                return
            entry["votes"] = {}
            entry["comment_ids"] = {}
            entry["scores_by_user"] = {}
            save_slug_map(SLUG_MAP)
            update_average(slug)
            print(f"✅ Reset votes for slug={slug}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        # ── Repair button message for a slug ─────────────────────────────────
        if self.path == "/repair":
            try:
                d = json.loads(body)
            except Exception:
                d = dict(urllib.parse.parse_qsl(body.decode()))
            slug = d.get("slug", "").strip()
            entry = SLUG_MAP.get(slug) if isinstance(SLUG_MAP.get(slug), dict) else None
            if not entry:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": f"slug '{slug}' not found"}).encode())
                return
            override_channel = d.get("channel_msg_id", "").strip()
            if override_channel:
                try:
                    entry["channel_msg_id"] = int(override_channel)
                    save_slug_map(SLUG_MAP)
                except ValueError:
                    pass
            override_id = d.get("button_msg_id", "").strip()
            if override_id:
                try:
                    button_msg_id = int(override_id)
                    entry["button_msg_id"] = button_msg_id
                    save_slug_map(SLUG_MAP)
                except ValueError:
                    button_msg_id = entry.get("button_msg_id")
            else:
                button_msg_id = entry.get("button_msg_id")
            button_text   = entry.get("button_text", "Оценить дизайн ✦")
            button_url    = f"{MINI_APP_URL}?startapp={slug}"
            votes = entry.get("votes", {})
            if votes:
                avg   = sum(votes.values()) / len(votes)
                count = len(votes)
                scores_by_user = entry.get("scores_by_user", {})
                crit_line = ""
                if scores_by_user:
                    keys = [("content", "Смысл"), ("usability", "Удобство"), ("visual", "Визуал"), ("idea", "Идея")]
                    parts = []
                    for key, label in keys:
                        vals = [s[key] for s in scores_by_user.values() if key in s]
                        if vals:
                            parts.append(f"{label} {round(sum(vals)/len(vals))}")
                    if parts:
                        crit_line = " | " + " · ".join(parts)
                text = f"⭐ <b>{round(avg)}/17</b>{crit_line}\n{count} {_vote_word(count)}"
            else:
                text = "·"
            markup = {"inline_keyboard": [[{"text": button_text, "url": button_url}]]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if not button_msg_id:
                self.wfile.write(json.dumps({"ok": False, "error": "button_msg_id not set"}).encode())
                return
            res_edit = tg("editMessageText", {
                "chat_id":      CHANNEL_ID,
                "message_id":   button_msg_id,
                "text":         text,
                "parse_mode":   "HTML",
                "reply_markup": markup,
            })
            err = (res_edit or {}).get("description", "")
            ok = bool(res_edit and (res_edit.get("ok") or "not modified" in err))
            print(f"[repair] editMessageText slug={slug} msg={button_msg_id} ok={ok} resp={res_edit}")
            self.wfile.write(json.dumps({"ok": ok, "button_msg_id": button_msg_id, "tg": res_edit}).encode())
            return

        # ── Upload avatar ─────────────────────────────────────────────────────
        if self.path == "/upload-avatar":
            ct = self.headers.get("Content-Type", "")
            fields, files = parse_multipart(body, ct)
            name = fields.get("avatar_name", "").strip()
            photo = files.get("avatar_photo")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if not name or not photo:
                self.wfile.write(json.dumps({"ok": False, "error": "name and photo required"}).encode())
                return
            import re as _re
            safe = _re.sub(r'[^\w\-]', '_', name) + ".jpg"
            with open(os.path.join(AVATARS_DIR, safe), "wb") as af:
                af.write(photo)
            avatars = load_avatars()
            avatars = [a for a in avatars if a["file"] != safe]
            avatars.append({"name": name, "file": safe})
            save_avatars(avatars)
            self.wfile.write(json.dumps({"ok": True, "file": safe}).encode())
            return

        # ── Delete avatar ─────────────────────────────────────────────────────
        if self.path == "/delete-avatar":
            d = json.loads(body)
            fname = os.path.basename(d.get("file", ""))
            avatars = [a for a in load_avatars() if a["file"] != fname]
            save_avatars(avatars)
            fpath = os.path.join(AVATARS_DIR, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
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
        final    = data.get("final", 0)
        # Use Telegram user_id as primary key — guaranteed unique across accounts.
        # Fall back to @username, then display name for anonymous/web users.
        user_id  = data.get("userId")
        username = str(user_id) if user_id else (data.get("username") or data.get("name") or "anon")
        chat_id  = DISCUSSION_ID if DISCUSSION_ID else CHANNEL_ID

        discussion_thread_id = resolve_discussion_thread(post_id)
        print(f"action={action} post_id={post_id} "
              f"discussion_thread_id={discussion_thread_id} "
              f"SLUG_MAP={SLUG_MAP} POST_MAP={POST_MAP}")

        entry = SLUG_MAP.get(post_id) if isinstance(SLUG_MAP.get(post_id), dict) else None

        if action == "delete":
            msg_to_delete = prev_id or (entry.get("comment_ids", {}).get(username) if entry else None)
            if msg_to_delete:
                tg("deleteMessage", {"chat_id": chat_id, "message_id": msg_to_delete})
            if entry:
                entry.get("votes", {}).pop(username, None)
                entry.get("comment_ids", {}).pop(username, None)
                save_slug_map(SLUG_MAP)
                update_average(post_id)
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        text = format_comment(data)

        # Server-side deduplication: if user already has a comment, edit it
        existing_comment_id = entry.get("comment_ids", {}).get(username) if entry else None
        if existing_comment_id:
            res = tg("editMessageText", {
                "chat_id":    chat_id,
                "message_id": existing_comment_id,
                "text":       text,
                "parse_mode": "HTML",
            })
        elif action == "update" and prev_id:
            res = tg("editMessageText", {
                "chat_id":    chat_id,
                "message_id": prev_id,
                "text":       text,
                "parse_mode": "HTML",
            })
        else:
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            if discussion_thread_id:
                payload["reply_to_message_id"]         = discussion_thread_id
                payload["allow_sending_without_reply"] = True
            res = tg("sendMessage", payload)

        comment_msg_id = res.get("result", {}).get("message_id") if res else None
        print(f"TG result: {res}")

        # Track vote, comment_id, and update average
        if entry is not None:
            entry.setdefault("votes", {})[username]          = final
            entry.setdefault("scores_by_user", {})[username] = data.get("scores", {})
            entry.setdefault("comment_ids", {})[username]    = comment_msg_id
            save_slug_map(SLUG_MAP)
            update_average(post_id)

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
