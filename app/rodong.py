"""Rodong Sinmun (rodong.rep.kp) source adapter.

Rodong navigation is `index.php?<base64-blob>`:
  - article blobs decode to "12@YYYY-MM-DD-seq@..."  (date is in the id)
  - category blobs decode to "1@@<section>@..."
The blob is treated as an opaque id; we extract links from listings and follow
them. Article pages use `.TitleP` (title), `.TextP` (body), `.photo-container`
(images). Pages are large and slow, so a longer client timeout is used.

NOTE: built from limited live inspection (Rodong is slow/flaky); selectors and
the image pattern are best-effort and validated on a live run.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from . import config

log = logging.getLogger("rodong")

# href="index.php?<blob>" — blob is base64 (standard or url-safe alphabet)
_INDEX_LINK = re.compile(r'index\.php\?([A-Za-z0-9+/=_\-]+)')
_DATE_ID = re.compile(r'(\d{4})-(\d{2})-(\d{2})-(\d+)')

# Rodong's own sections -> (key, english, sort). Shared key space with KCNA where
# the topic matches, so dedup/merge keeps tags consistent.
RODONG_CATEGORIES = {
    "혁명활동소식": ("leader", "Supreme Leader's Activities", 1),
    "인민을 위한 정치": ("politics", "Politics for the People", 11),
    "전진하는 조선": ("advancing", "Advancing Korea", 12),
    "사회문화생활": ("social", "Social Life", 10),
    "유구한 력사,찬란한 문화": ("culture", "History & Culture", 13),
    "사진": ("photos", "Photos", 14),
    "동영상": ("video", "Video", 15),
}


@dataclass
class RodongArticle:
    rid: str
    href: str
    title: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    published_date: Optional[date] = None
    image_urls: list = field(default_factory=list)
    category_keys: set = field(default_factory=set)


def _decode(blob: str) -> str:
    for alt in (blob, blob.replace("-", "+").replace("_", "/")):
        try:
            pad = "=" * (-len(alt) % 4)
            return base64.b64decode(alt + pad).decode("utf-8", "replace")
        except Exception:
            continue
    return ""


def _clean(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _rid_and_date(blob: str) -> tuple[Optional[str], Optional[date]]:
    dec = _decode(blob)
    if not dec.startswith("12@"):
        return None, None
    m = _DATE_ID.search(dec)
    if not m:
        return None, None
    y, mo, da, seq = m.groups()
    rid = f"{y}-{mo}-{da}-{seq}"
    try:
        return rid, date(int(y), int(mo), int(da))
    except ValueError:
        return rid, None


def extract_article_links(html: str) -> list[tuple[str, str, Optional[date]]]:
    """Return unique (href, rid, date) for article links found in a listing."""
    out: dict[str, tuple[str, str, Optional[date]]] = {}
    for m in _INDEX_LINK.finditer(html):
        blob = m.group(1)
        rid, d = _rid_and_date(blob)
        if rid and rid not in out:
            out[rid] = (f"index.php?{blob}", rid, d)
    return list(out.values())


# Rodong's photo-gallery API. The article page lazy-loads each photo via
#   POST index.php?<PHOTO_ACTION_BLOB>   (the blob decodes to "05@@@@")
# returning a small HTML fragment whose <img src="index.php?<blob>"> points at
# the full-resolution JPEG. iThemeID is the field right after the rid in the
# article blob (12@<rid>@<themeID>@...).
PHOTO_ACTION_BLOB = "MDVAQEBA"


def theme_id_from_href(href: str) -> Optional[str]:
    m = _INDEX_LINK.search(href or "")
    if not m:
        return None
    parts = _decode(m.group(1)).split("@")
    return parts[2] if len(parts) > 2 and parts[2].isdigit() else None


def photo_request_body(rid: str, theme_id: Optional[str], photo_no: int,
                       pub_date: str) -> dict:
    return {
        "chAction": "P", "strSchKey": "",
        "strNewsID": rid, "iThemeID": str(theme_id or ""),
        "iPhotoNo": str(photo_no), "iSelType": "0",
        "dPublish": pub_date or "", "iViewWidth": "1500", "iViewHeight": "1500",
    }


def extract_photo_img_src(fragment_html: str) -> Optional[str]:
    """The <img src> (relative index.php?<blob>) from a photo-API fragment."""
    img = HTMLParser(fragment_html).css_first("img")
    if not img:
        return None
    src = img.attributes.get("src") or ""
    return src or None


def extract_category_links(html: str) -> list[tuple[str, str]]:
    """Return (href, korean_label) for category links (blob decodes to '1@@')."""
    tree = HTMLParser(html)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        m = _INDEX_LINK.search(href)
        if not m:
            continue
        if not _decode(m.group(1)).startswith("1@@"):
            continue
        label = _clean(a.text())
        if href not in seen and label:
            seen.add(href)
            out.append((href, label))
    return out


def extract_logo_src(home_html: str) -> Optional[str]:
    """Best-effort masthead logo from the Rodong homepage. Prefers images whose
    src/alt mention a logo/Rodong/로동; otherwise falls back to the first image."""
    tree = HTMLParser(home_html)
    first: Optional[str] = None
    for img in tree.css("img"):
        src = img.attributes.get("src") or img.attributes.get("data-src") or ""
        if not src:
            continue
        if first is None:
            first = src
        alt = (img.attributes.get("alt") or "").lower()
        s = src.lower()
        if "logo" in s or "logo" in alt or "rodong" in s or "로동" in alt or "rodong" in alt:
            return src
    return first


def parse_article(html: str, rid: str, d: Optional[date]) -> RodongArticle:
    tree = HTMLParser(html)
    art = RodongArticle(rid=rid, href="", published_date=d)

    title_el = tree.css_first(".TitleP") or tree.css_first(".article-modal-header")
    art.title = _clean(title_el.text()) if title_el else None

    container = tree.css_first(".article-content") or tree.body
    paras = [_clean(p.text()) for p in (container.css(".TextP") if container else [])]
    paras = [p for p in paras if p]
    if not paras:  # fallback: any .TextP on the page
        paras = [t for t in (_clean(p.text()) for p in tree.css(".TextP")) if t]
    art.body_text = "\n\n".join(paras)
    if container:
        art.body_html = container.html

    # date fallback from the page if the id had none
    if art.published_date is None:
        dm = re.search(r"(20\d{2})[.\-](\d{1,2})[.\-](\d{1,2})", tree.body.text() if tree.body else "")
        if dm:
            try:
                art.published_date = date(*(int(x) for x in dm.groups()))
            except ValueError:
                pass

    # images inside the article/photo area; skip obvious UI assets.
    # Rodong inlines article photos as base64 `data:` URIs — keep those verbatim
    # (they carry the bytes; the store step decodes and saves them). Only real
    # relative paths get resolved against the site root.
    seen: set[str] = set()
    scope = tree.css(".photo-container img") + (container.css("img") if container else [])
    for img in scope:
        src = img.attributes.get("src") or img.attributes.get("data-src") or ""
        if not src:
            continue
        if src.startswith("data:"):
            url = src
        else:
            if re.search(r"(logo|icon|btn|menu|banner|sprite|blank)", src, re.I):
                continue
            url = urljoin(config.RODONG_BASE_URL + "/ko/", src)
        if url not in seen:
            seen.add(url)
            art.image_urls.append(url)
    return art
