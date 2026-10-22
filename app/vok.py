"""Voice of Korea (vok.rep.kp) video helpers.

VOK plays videos through an HLS iframe at ``/hls/player/<id>``. That player page
is *server-rendered* and embeds the tokenized ``.m3u8`` playlist URL directly in
its HTML, so a plain HTTP GET (no JavaScript) yields a fresh, time-limited stream
URL we can hand straight to ffmpeg. The token is a ~5-minute signed query param,
minted per page load — so we must download promptly after fetching it.
"""
from __future__ import annotations

import re

from . import config

# The tokenized playlist URL as it appears in the player HTML. It may be written
# protocol-relative ("//host/..") and VOK emits a doubled slash before "hls".
_M3U8_RE = re.compile(r'''(?:https?:)?//[^\s"'<>]+?\.m3u8\?token=[^\s"'<>]+''')
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def player_path(video_id: str) -> str:
    """Path (relative to VOK_BASE_URL) of the HLS player iframe page."""
    return f"/hls/player/{video_id}"


def detail_url(video_id: str) -> str:
    """Canonical public page for a VOK video (used as the article source link)."""
    return f"{config.VOK_BASE_URL}/index.php/detail_com/vi_video/{video_id}"


def extract_m3u8_url(player_html: str) -> str | None:
    """Pull the fresh, tokenized index.m3u8 URL out of the player page HTML.

    Returns an absolute http(s) URL, leaving the path (including VOK's doubled
    "//hls" slash, which the origin expects) untouched."""
    m = _M3U8_RE.search(player_html or "")
    if not m:
        return None
    url = m.group(0)
    if url.startswith("//"):
        url = "http:" + url
    return url


def extract_title(player_html: str, fallback: str) -> str:
    """Best-effort human title from the player page; the <title> looks like
    "ike260528004 - 동영상재생기", so strip the trailing player label."""
    m = _TITLE_RE.search(player_html or "")
    if not m:
        return fallback
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    if " - " in t:
        t = t.split(" - ")[0].strip()
    return t or fallback
