"""HTML parsing for KCNA pages, using the selectors validated in SCRAPING_NOTES.md.

Pure functions over HTML strings — no network — so they are trivially unit
testable against saved fixtures.

KCNA rebuilt its site (re-validated live 2026-10-23): the `.kcmsf` routes are
gone. Pages now hang off `/{lang}/{kind}/detail/<32 hex>` (article, gallery,
video) with `/{lang}/{kind}/list/<32 hex>` listings, and one image route,
`/photo/<128 hex>`. Two consequences this module absorbs:

* Article pages carry **no year** — the dateline reads "(평양 10월 20일발
  조선중앙통신)". The year only appears beside the link on the homepage and on
  listing pages (`[2026.10.20.]`), so `parse_article` accepts a `listing_date`
  and a `year_hint`.
* Photos sit on a separate gallery page behind the article's camera button, and
  `/photo/…` answers 200 with an **empty body** unless the request carries a
  kcna.kp `Referer` (handled by `KcnaClient` + `_kcna_photo_urls`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from selectolax.parser import HTMLParser

_LANG = r"kp|en|cn|ru|es|jp"
_HEX = r"[0-9a-f]{32}"
# Photo/video ids are long hex digests (64 chars observed). Written as a
# constant, not inline: `{40,}` inside an f-string is a field, not a quantifier.
_MEDIA_ID = r"[0-9a-f]{40,}"

# /{lang}/article/detail/<32 hex>   (was /{lang}/article/q/<hash>.kcmsf)
ARTICLE_RE = re.compile(rf"/(?P<lang>{_LANG})/article/detail/(?P<hash>{_HEX})")
# Legacy shape, still stored on rows scraped before the rebuild and in links
# people pasted; accepted by ingest_url so old URLs keep resolving.
LEGACY_ARTICLE_RE = re.compile(rf"/(?:{_LANG})/article/q/(?P<hash>[a-f0-9]{{16,}})\.kcmsf")
# /{lang}/article/list/<32 hex> — a category listing page
LIST_RE = re.compile(rf"/(?P<lang>{_LANG})/article/list/(?P<hash>{_HEX})")
# /{lang}/gallery/detail/<32 hex> — the photo page behind an article's camera icon
GALLERY_RE = re.compile(rf"/(?P<lang>{_LANG})/gallery/detail/(?P<hash>{_HEX})")
# /{lang}/video/detail/<32 hex> plus the stream files embedded in it
VIDEO_PAGE_RE = re.compile(rf"/(?P<lang>{_LANG})/video/detail/(?P<hash>{_HEX})")
VIDEO_STREAM_RE = re.compile(rf"/(?P<lang>{_LANG})/video/stream/(?P<id>{_MEDIA_ID})")
# /photo/<long hex> — the only image route left on the rebuilt site.
PHOTO_RE = re.compile(rf"/photo/(?P<id>{_MEDIA_ID})")

# "[2026.10.20.]" — the stamp beside every link on the homepage and listings.
ITEM_DATE_RE = re.compile(r"\[\s*(20\d{2})\.\s?(\d{1,2})\.\s?(\d{1,2})\.?\s*\]")
# Legacy/alternate form still seen in body text on some pages.
DATE_RE = re.compile(r"(20\d{2})\.\s?(\d{1,2})\.\s?(\d{1,2})")
# Korean dateline month/day, no year: "(평양 10월 20일발 조선중앙통신)"
KO_MD_RE = re.compile(r"(\d{1,2})\s?월\s?(\d{1,2})\s?일")
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
    # Path of the photo page behind the article's camera button, if it has one.
    gallery_url: Optional[str] = None


@dataclass
class ListedArticle:
    """One entry exactly as it appears on the homepage or a listing page."""
    hash: str
    lang: str
    published_date: Optional[date] = None
    title: Optional[str] = None


def article_path(lang: str, hash_: str) -> str:
    """The canonical path of an article — the one place the URL shape is built."""
    return f"/{lang}/article/detail/{hash_}"


def extract_article_links(html: str) -> list[tuple[str, str]]:
    """Return unique (lang, hash) pairs for every article URL in the HTML."""
    seen: dict[str, tuple[str, str]] = {}
    for m in ARTICLE_RE.finditer(html):
        seen[m.group("hash")] = (m.group("lang"), m.group("hash"))
    return list(seen.values())


def extract_categories(html: str) -> list[tuple[str, str]]:
    """Return (listing_path, korean_label) for each category link on a page
    (e.g. the homepage navigation: 중요소식, 문건, 국내소식 …)."""
    tree = HTMLParser(html)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not LIST_RE.search(href):
            continue
        label = _clean(a.text())
        if href not in seen and label:
            seen.add(href)
            out.append((href, label))
    return out


def extract_listing(html: str) -> list[ListedArticle]:
    """Every article link on a homepage/listing page, with its date stamp.

    Links look like

        <h5 class="block">
          <a href="/kp/article/detail/<hash>">제목</a>
          <span class="publish_date"><nobr>[2026.10.20.]</nobr></span>
        </h5>

    The stamp is a sibling inside the same heading, so the date is read off the
    anchor's parent and nothing else — walking further up would let one block's
    date leak onto every link in it.
    """
    tree = HTMLParser(html)
    out: list[ListedArticle] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        m = ARTICLE_RE.search(href)
        if not m:
            continue
        h = m.group("hash")
        if h in seen:
            continue
        seen.add(h)
        stamp = None
        parent = a.parent
        if parent is not None:
            pm = ITEM_DATE_RE.search(parent.text() or "")
            if pm:
                stamp = _safe_date(int(pm.group(1)), int(pm.group(2)), int(pm.group(3)))
        out.append(ListedArticle(hash=h, lang=m.group("lang"),
                                 published_date=stamp, title=_clean(a.text())))
    return out


def extract_gallery_urls(html: str) -> list[str]:
    """Gallery (photo) page paths referenced by a page, in document order."""
    out: list[str] = []
    for m in GALLERY_RE.finditer(html):
        if m.group(0) not in out:
            out.append(m.group(0))
    return out


def extract_photo_urls(html: str) -> list[str]:
    """/photo/<hex> image paths referenced by a page, in document order.

    This is how a gallery page hands back its pictures. Each path must then be
    fetched with a kcna.kp page as Referer — without one the origin answers
    200 with an empty body.
    """
    out: list[str] = []
    tree = HTMLParser(html)
    for img in tree.css("img"):
        for attr in ("src", "data-src", "data-original"):
            val = img.attributes.get(attr)
            if val and PHOTO_RE.search(val) and val not in out:
                out.append(val)
    if not out:  # referenced outside <img> tags (inline styles, JSON blobs)
        for m in PHOTO_RE.finditer(html):
            if m.group(0) not in out:
                out.append(m.group(0))
    return out


def _clean(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _safe_date(year: int, month: int, day: int, today: Optional[date] = None) -> Optional[date]:
    """Build a date from a year-less stamp, backing the year off when the math
    lands in the future (scraping a late-December dateline in January)."""
    today = today or date.today()
    for y in (year, year - 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if d <= today + timedelta(days=3):
            return d
    return None


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


def _resolve_date(art: ParsedArticle, lang: str, *, year_hint: Optional[int],
                  listing_date: Optional[date], doc_text: str) -> Optional[date]:
    """Publication date, best source first: the `[2026.10.20.]` stamp from the
    listing the article was discovered on, then the dateline (month/day plus a
    year hint), then anything date-shaped left on the page."""
    if listing_date:
        return listing_date
    year = year_hint or date.today().year
    if lang == "kp":
        m = KO_MD_RE.search(art.dateline or "")
        if m:
            d = _safe_date(year, int(m.group(1)), int(m.group(2)))
            if d:
                return d
        return _parse_date(doc_text)
    d = _parse_en_date(art.dateline or "")
    if d:
        return d
    m = EN_DATE_RE.search(art.dateline or "")
    if m and _EN_MONTHS.get(m.group(1).lower()):
        d = _safe_date(year, _EN_MONTHS[m.group(1).lower()], int(m.group(2)))
        if d:
            return d
    return _parse_en_date(doc_text)


def parse_article(html: str, lang: str, hash_: str, *,
                  year_hint: Optional[int] = None,
                  listing_date: Optional[date] = None) -> ParsedArticle:
    """Parse one `/{lang}/article/detail/<hash>` page.

    `listing_date` — the `[2026.10.20.]` stamp the article was found under —
    wins over the dateline, because article pages themselves print no year.
    """
    tree = HTMLParser(html)
    art = ParsedArticle(hash=hash_, lang=lang)

    # The article renders inside <article>. Listing pages use a
    # <div class="article">, so the tag selector never picks them up.
    scope = tree.css_first("article") or tree.css_first("main") or tree.body
    if scope is None:
        return art

    # Headline: the first non-empty <h1>; a second one carries the sub-headline
    # (the template always emits two, the empty one is skipped).
    h1s = [t for t in (_clean(n.text()) for n in scope.css("h1")) if t]
    if h1s:
        art.title = h1s[0]
        if len(h1s) > 1:
            art.subtitle = h1s[1]
    if not art.title:
        # Fallback: <title> is "조선중앙통신 | <headline>".
        node = tree.css_first("title")
        head = _clean(node.text()) if node else None
        if head and "|" in head:
            art.title = _clean(head.rsplit("|", 1)[1])

    paras_nodes = [p for p in scope.css("p") if _clean(p.text())]
    paras = [_clean(p.text()) for p in paras_nodes]
    if paras:
        art.body_text = "\n\n".join(paras)
        art.body_html = "".join(p.html for p in paras_nodes)
        art.dateline = _extract_dateline(paras, lang)

    doc_text = tree.body.text() if tree.body else ""
    art.published_date = _resolve_date(art, lang, year_hint=year_hint,
                                       listing_date=listing_date, doc_text=doc_text)

    # Media: inline photos (rare now), the camera-button gallery link, video
    # streams. src OR lazy-loaded data-src.
    media_seen: set[str] = set()
    for img in tree.css("img"):
        for attr in ("src", "data-src", "data-original"):
            val = img.attributes.get(attr)
            if val and PHOTO_RE.search(val) and val not in media_seen:
                media_seen.add(val)
                art.image_urls.append(val)
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if GALLERY_RE.search(href) and not art.gallery_url:
            art.gallery_url = href
    for tag in tree.css("video, source"):
        val = tag.attributes.get("src")
        if val and VIDEO_STREAM_RE.search(val) and val not in media_seen:
            media_seen.add(val)
            art.video_urls.append(val)

    return art
