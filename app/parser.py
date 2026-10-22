"""HTML parsing for KCNA pages, using the selectors validated in SCRAPING_NOTES.md.

Pure functions over HTML strings — no network — so they are trivially unit
testable against saved fixtures.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from selectolax.parser import HTMLParser

# /{lang}/article/q/<32hex>.kcmsf
ARTICLE_RE = re.compile(r"/(?P<lang>kp|en|cn|ru|es|jp)/article/q/(?P<hash>[a-f0-9]{16,})\.kcmsf")
# /{lang}/image|video/q/<hash>.kcmsf
MEDIA_RE = re.compile(r"/(?:kp|en|cn|ru|es|jp)/(?P<kind>image|video)/q/[a-f0-9]+\.kcmsf")
DATE_RE = re.compile(r"(20\d{2})\.\s?(\d{1,2})\.\s?(\d{1,2})")
# English KCNA dateline: "Pyongyang, June 10 (KCNA) -- ..." (sometimes "... Juche 115 (2026) ...")
EN_DATELINE_RE = re.compile(r"^(.{0,60}?\(KCNA\))", re.DOTALL)
EN_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{1,2})\b(?:.{0,30}?\((20\d{2})\))?",
    re.IGNORECASE,
)
_EN_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


@dataclass
class ParsedArticle:
    hash: str
    lang: str
    title: Optional[str] = None
    subtitle: Optional[str] = None
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    published_date: Optional[date] = None
    dateline: Optional[str] = None
    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)


def extract_article_links(html: str) -> list[tuple[str, str]]:
    """Return unique (lang, hash) pairs for every article URL in the HTML."""
    seen: dict[str, tuple[str, str]] = {}
    for m in ARTICLE_RE.finditer(html):
        seen[m.group("hash")] = (m.group("lang"), m.group("hash"))
    return list(seen.values())


def extract_categories(html: str) -> list[tuple[str, str]]:
    """Return (category_path, korean_label) for each article-category link on a
    page (e.g. the homepage navigation)."""
    tree = HTMLParser(html)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if "/category/articles/q/" not in href:
            continue
        label = _clean(a.text())
        if href not in seen and label:
            seen.add(href)
            out.append((href, label))
    return out


def _clean(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _parse_date(text: str) -> Optional[date]:
    """Korean-style YYYY.MM.DD. Returns None if absent."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _parse_en_date(text: str) -> Optional[date]:
    """English 'Month DD ... (YYYY)'. Year only when present, else None."""
    m = EN_DATE_RE.search(text or "")
    if not m:
        return None
    month = _EN_MONTHS.get(m.group(1).lower())
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else None
    if not month or year is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_dateline(paras: list[str], lang: str) -> Optional[str]:
    if not paras:
        return None
    first = paras[0]
    if lang == "kp":
        if first.startswith("(") and "통신" in first[:40]:
            return first
        return None
    # English: dateline is a prefix of the first paragraph, ending in "(KCNA)".
    m = EN_DATELINE_RE.match(first)
    return m.group(1).strip() if m else None


def parse_article(html: str, lang: str, hash_: str) -> ParsedArticle:
    tree = HTMLParser(html)
    art = ParsedArticle(hash=hash_, lang=lang)

    main = tree.css_first(".article-main-title")
    art.title = _clean(main.text()) if main else None
    sub = tree.css_first(".article-sub-title")
    art.subtitle = _clean(sub.text()) if sub else None

    body = tree.css_first(".article-content-body")
    if body:
        art.body_html = body.html
        paras = [_clean(p.text()) for p in body.css("p")]
        paras = [p for p in paras if p]
        art.body_text = "\n\n".join(paras) if paras else _clean(body.text())
        art.dateline = _extract_dateline(paras, lang)

    # Date: Korean YYYY.MM.DD anywhere; English Month DD (YYYY) if a year is given.
    doc_text = tree.body.text() if tree.body else ""
    if lang == "kp":
        art.published_date = _parse_date(art.dateline or "") or _parse_date(doc_text)
    else:
        art.published_date = _parse_en_date(art.dateline or "") or _parse_en_date(doc_text)

    # Media: pattern-based over the whole document; src OR lazy-loaded data-src.
    media_seen: set[str] = set()
    for img in tree.css("img"):
        for attr in ("src", "data-src", "data-original"):
            val = img.attributes.get(attr)
            if not val:
                continue
            mm = MEDIA_RE.search(val)
            if mm and val not in media_seen:
                media_seen.add(val)
                (art.image_urls if mm.group("kind") == "image" else art.video_urls).append(val)
    for tag in tree.css("video, source"):
        val = tag.attributes.get("src")
        if val and MEDIA_RE.search(val) and val not in media_seen:
            media_seen.add(val)
            art.video_urls.append(val)

    return art
