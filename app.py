
import re
import time
import logging
import os
from urllib.parse import urlparse

import requests
import yt_dlp
from flask import Flask, request, jsonify, Response, send_from_directory, abort

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)

IG_URL = re.compile(
    r"^https?://(www\.)?instagram\.com/(reel|reels|p|tv)/[\w-]+/?(?:\?.*)?$",
    re.I,
)

CDN_DOMAINS = ("cdninstagram.com", "fbcdn.net")
HITS = {}


def limited(ip):
    now = time.time()
    recent = [
        stamp for stamp in HITS.get(ip, [])
        if now - stamp < 60
    ]
    recent.append(now)
    HITS[ip] = recent
    return len(recent) > 10


def cdn_only(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        allowed = any(
            host == domain or host.endswith("." + domain)
            for domain in CDN_DOMAINS
        )

        return parsed.scheme == "https" and allowed

    except (TypeError, ValueError):
        return False


def is_rate_limit(error):
    message = str(error).lower()

    return (
        "http error 429" in message
        or "too many requests" in message
        or "rate limit" in message
        or "rate-limit" in message
    )


@app.get("/")
def home():
    return send_from_directory(".", "index.html")


@app.post("/api/info")
def info():

    ip = request.headers.get(
        "X-Forwarded-For",
        request.remote_addr or ""
    ).split(",")[0].strip()

    if limited(ip):
        return jsonify(
            error="Too many requests. Please wait before trying again."
        ), 429

    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url", "")).strip()

    if not IG_URL.fullmatch(url):
        return jsonify(
            error="Please paste a valid Instagram Reel/post link."
        ), 400

    try:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,
        }

        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(url, download=False)

        if not data:
            return jsonify(
                error="Instagram did not return video information."
            ), 422

        if data.get("entries"):
            data = next(
                (item for item in data["entries"] if item),
                None
            )

        if not data or not data.get("url"):
            return jsonify(
                error="No downloadable video was returned."
            ), 422

        return jsonify(
            video=data["url"],
            thumbnail=data.get("thumbnail"),
            title=(data.get("title") or "Instagram video")[:90]
        )

    except Exception as exc:

        logger.exception("Instagram fetching failed")

        if is_rate_limit(exc):
            return jsonify(
                error=(
                    "Instagram is temporarily limiting requests "
                    "(HTTP 429). Please try again later."
                )
            ), 429

        return jsonify(
            error=(
                "Could not fetch this post. "
                "Check that it is public and try again later."
            )
        ), 422


@app.get("/api/file")
def download_file():

    url = request.args.get("u", "")

    if not cdn_only(url):
        abort(400)

    try:
        upstream = requests.get(
            url,
            stream=True,
            timeout=(10, 60)
        )
        upstream.raise_for_status()

    except requests.RequestException:
        logger.exception("Video download failed")

        return jsonify(
            error="Video server is temporarily unavailable."
        ), 502

    headers = {
        "Content-Disposition": 'attachment; filename="reel.mp4"',
        "X-Content-Type-Options": "nosniff",
    }

    if upstream.headers.get("Content-Length"):
        headers["Content-Length"] = upstream.headers["Content-Length"]

    return Response(
        upstream.iter_content(chunk_size=65536),
        content_type=upstream.headers.get(
            "Content-Type", "video/mp4"
        ),
        headers=headers,
        direct_passthrough=True,
    )


@app.get("/api/thumb")
def thumb():

    url = request.args.get("u", "")

    if not cdn_only(url):
        abort(400)

    try:
        upstream = requests.get(url, timeout=(10, 20))
        upstream.raise_for_status()

    except requests.RequestException:
        logger.exception("Thumbnail request failed")

        return jsonify(
            error="Thumbnail is temporarily unavailable."
        ), 502

    return Response(
        upstream.content,
        content_type=upstream.headers.get(
            "Content-Type", "image/jpeg"
        ),
        headers={"Cache-Control": "public, max-age=3600"}
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )
    
