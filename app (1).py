import re, time
from urllib.parse import urlparse
import requests
import yt_dlp
from flask import Flask, request, jsonify, Response, send_from_directory, abort

app = Flask(__name__, static_folder=None)
IG_URL = re.compile(r"^https?://(www\.)?instagram\.com/(reel|reels|p|tv)/[\w-]+", re.I)
CDN_OK = ("cdninstagram.com", "fbcdn.net")
HITS = {}  # ip -> [timestamps]; simple rate limit (10 requests / minute)

def limited(ip):
    now = time.time()
    h = [t for t in HITS.get(ip, []) if now - t < 60]
    h.append(now); HITS[ip] = h
    return len(h) > 10

def cdn_only(u):
    host = urlparse(u).hostname or ""
    return u.startswith("https://") and host.endswith(CDN_OK)

@app.get("/")
def home():
    return send_from_directory(".", "index.html")

@app.post("/api/info")
def info():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]
    if limited(ip):
        return jsonify(error="Too many requests. Wait a minute."), 429
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not IG_URL.match(url):
        return jsonify(error="Please paste a valid Instagram Reel/post link."), 400
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True}) as y:
            d = y.extract_info(url, download=False)
        if d.get("entries"):
            d = d["entries"][0]
        return jsonify(video=d["url"], thumbnail=d.get("thumbnail"), title=(d.get("title") or "")[:90])
    except Exception:
        return jsonify(error="Couldn't fetch this post. It may be private or removed."), 422

@app.get("/api/file")
def file():
    u = request.args.get("u", "")
    if not cdn_only(u): abort(400)
    r = requests.get(u, stream=True, timeout=20)
    h = {"Content-Disposition": 'attachment; filename="reel.mp4"'}
    if r.headers.get("Content-Length"): h["Content-Length"] = r.headers["Content-Length"]
    return Response(r.iter_content(65536), content_type="video/mp4", headers=h)

@app.get("/api/thumb")
def thumb():
    u = request.args.get("u", "")
    if not cdn_only(u): abort(400)
    r = requests.get(u, timeout=15)
    return Response(r.content, content_type=r.headers.get("Content-Type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=3600"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
