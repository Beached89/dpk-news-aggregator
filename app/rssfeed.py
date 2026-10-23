"""English-language North Korea sources published over RSS (38 North, Daily NK).

Why a feed instead of HTML crawling
----------------------------------
Both sites are WordPress and publish *full-text* RSS 2.0: the article body is in
``content:encoded``, the byline in ``dc:creator``, the section in ``<category>``
and the publication moment in ``pubDate``. Their ``robots.txt`` permits it
(38north: ``Disallow:`` - nothing; dailynk: only ``/wp-admin/``, ``/?s=``,
``/search/``). So ingestion needs no cookie warm-up, no CSRF token, no JS and no
headless browser - unlike KCNA (year-less datelines, Referer-guarded photos) or
Rodong (base64 blobs, POST-only gallery API). A feed is the polite *and* the
robust option: WordPress keeps the RSS schema stable far longer than page markup.

Traps this module handles
-------------------------
* ``content:encoded`` is whole-page HTML, not an article body: figures and their
  captions, embedded tweets/YouTube as ``<iframe>``, ad divs, shared-post
  buttons and a trailing "The post ... appeared first on ..." line. All stripped.
* WordPress upload URLs can contain Hangul (Daily NK keeps uploads under Korean
  directory names). Raw IRIs blow up on ASCII-only HTTP stacks, so paths are
  percent-encoded before they are ever requested.
* Feeds escape their HTML on some hosts, so unescaping happens before image and
  text extraction.
* Some items ship with no image at all, so the caller must *not* make a photo a
  publish requirement the way the KCNA/Rodong paths do.
* ``pubDate`` is an RFC 2822 string ("Thu, 22 Oct 2026 14:19:09 +0000"), not ISO.
"""
from __future__ import annotations

import hashlib
import html as html_mod
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

from selectolax.parser import HTMLParser

# Namespaces WordPress/RSS emit. Missing ones simply never match.
_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}

# Block-level containers whose text makes up the article, in document order.
_TEXT_SELECTORS = "p, li, h2, h3, blockquote"

# Everything that is chrome rather than prose. The paired-tag regexes are
# non-greedy and none of these nest inside each other in real WordPress output.
_DROP_PAIRS = ("script", "style", "noscript", "iframe", "svg", "figure",
               "figcaption", "form", "nav", "footer", "header", "aside")
_DROP_SELF_CLOSING = ("iframe", "embed", "object", "source", "br")

# Subset dropped when scanning for images. <figure>/<figcaption> are absent on
# purpose: that is where WordPress keeps the article's own images.
_MEDIA_DROP_PAIRS = ("script", "style", "noscript", "iframe", "svg", "form",
                     "nav", "footer", "header", "aside")

# WordPress "The post X appeared first on Y" boilerplate, share widgets, ads.
_BOILERPLATE = re.compile(
    r"(appeared first on|follow our newsletter|subscribe to|related posts"
    r"|sign up for|share this|copy link)", re.I)

# UI assets that are never article content.
_NOT_ARTICLE = re.compile(
    r"(avatar|gravatar|smiley|emoji|icon|logo|banner|sprite|badge|placeholder|"
    r"blank\.|pixel|spacer|\.(svg|ico)(\?|$))", re.I)

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_SRC_ATTR = re.compile(r"(?:\s(?:data-)?src)\s*=\s*[\"']([^\"']+)[\"']", re.I)
_TAG_PAIR = re.compile(r"<(%s)\b[^>]*>.*?</\1\s*>" % "|".join(_DROP_PAIRS),
                       re.I | re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_WS = re.compile(r"[ \t\r\f\v\u00a0\u200b]+")


@dataclass
class FeedItem:
    """One RSS item, already reduced to what the pipeline needs."""
    id: str                       # stable 32-hex id derived from the permalink
    link: str
    title: Optional[str] = None
    published: Optional[date] = None
    published_dt: Optional[datetime] = None
    author: Optional[str] = None
    categories: list = field(default_factory=list)    # original labels
    html_body: Optional[str] = None                   # cleaned article HTML
    body_text: Optional[str] = None                   # paragraphs, \n\n joined
    excerpt: Optional[str] = None                     # plain-text summary

    @property
    def best_text(self) -> str:
        return self.body_text or self.excerpt or ""


# ------------------------------------------------------------------ ids / dates
def article_id(link: str) -> str:
    """Stable 32-hex article id, mirroring KCNA's hash-shaped keys.

    These sites publish no content hash of their own, so the permalink (query and
    fragment stripped, host lower-cased) is the identity. The same URL always
    yields the same id, which is what makes re-runs idempotent and lets the
    export dedup group a story across sources.
    """
    parsed = urlparse((link or "").strip())
    canon = urlunparse((parsed.scheme.lower(), parsed.netloc.lower(),
                        parsed.path, "", "", ""))
    return hashlib.md5(canon.encode("utf-8")).hexdigest()


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """RFC 2822 (RSS pubDate) or ISO 8601 (Atom) -> aware UTC datetime."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ------------------------------------------------------------------- html tools
def strip_chrome(raw_html: str) -> str:
    """Drop scripts, styles, figures/captions, embeds and comments."""
    text = _COMMENT.sub(" ", raw_html or "")
    for _ in range(3):                    # a pass can expose a pair it hid
        stripped = _TAG_PAIR.sub(" ", text)
        if stripped == text:
            break
        text = stripped
    for tag in _DROP_SELF_CLOSING:
        text = re.sub(r"<%s\b[^>]*/?>" % tag, " ", text, flags=re.I)
    return text


def strip_media_chrome(raw_html: str) -> str:
    """Drop the markup that can never hold an article asset - but keep <figure>.

    This is the asymmetry that matters: WordPress wraps article images in
    ``<figure class="wp-block-image">``, so the variant used for *text* throws
    figures away (with their captions) while this one must not, or a satellite
    piece comes back with zero images. (That was a real regression caught on a
    live feed: 38 North publishes every figure inside one.)
    """
    text = _COMMENT.sub(" ", raw_html or "")
    pattern = r"<(%s)\b[^>]*>.*?</\1\s*>" % "|".join(_MEDIA_DROP_PAIRS)
    for _ in range(3):
        stripped = re.sub(pattern, " ", text, flags=re.I | re.S)
        if stripped == text:
            break
        text = stripped
    return text


def _clean(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    collapsed = _WS.sub(" ", text).strip()
    return collapsed or None


def html_to_text(raw_html: str) -> str:
    """Article HTML -> paragraph text joined by blank lines.

    The viewer splits body_text on blank lines, so that join is part of the
    contract with publish.py, not a cosmetic choice.
    """
    cleaned = strip_chrome(raw_html)
    if not cleaned.strip():
        return ""
    tree = HTMLParser(cleaned)
    paras: list[str] = []
    for node in tree.css(_TEXT_SELECTORS):
        piece = _clean(html_mod.unescape(node.text() or ""))
        if piece and not _BOILERPLATE.search(piece):
            paras.append(piece)
    if not paras:                          # markup with no block elements at all
        flat = _clean(html_mod.unescape(tree.body.text() if tree.body else ""))
        if flat:
            paras = [flat]
    return "\n\n".join(paras)


def encode_iri(url: str) -> str:
    """Percent-encode non-ASCII characters in a URL, leaving the rest alone.

    Daily NK serves uploads from Hangul paths, and an unencoded IRI fails at the
    socket layer on ASCII-only stacks (observed: "'ascii' codec can't encode
    characters"). Only the path is escaped; an already-escaped URL is unchanged
    because ``%`` stays in the safe set.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.scheme and not parsed.netloc:
        return url
    return urlunparse((parsed.scheme, parsed.netloc,
                       quote(parsed.path, safe="/%:~@"),
                       parsed.params, parsed.query, parsed.fragment))


def images_from_html(raw_html: str, base: str = "") -> list[str]:
    """Absolute, de-duplicated article image URLs in document order.

    Uses `strip_media_chrome` (which keeps <figure>) rather than `strip_chrome`
    (which drops it) - see that function for why the distinction decides whether
    any images survive at all. ``srcset`` variants are deliberately not followed:
    the plain ``src`` is what WordPress ships at a sane size, and guessing at
    ``-1024x`` suffixes trades a little bandwidth for 404s.
    """
    cleaned = strip_media_chrome(html_mod.unescape(raw_html or ""))
    out: list[str] = []
    seen: set[str] = set()
    for tag in _IMG_TAG.finditer(cleaned):
        candidates = [c.strip() for c in _SRC_ATTR.findall(tag.group(0)) if c.strip()]
        # A lazy-loaded tag carries a data: placeholder in src with the real file
        # in data-src, so take the first candidate that is actually fetchable.
        src = next((c for c in candidates if not c.startswith("data:")), "")
        if not src or _NOT_ARTICLE.search(src):
            continue
        absolute = encode_iri(urljoin(base, src)) if base else encode_iri(src)
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out



# ------------------------------------------------------------------- categories
# WordPress tags are free-form and noisy. Map the ones that matter onto stable
# keys (so the same beat tags identically from either site) and slug the rest.
CATEGORY_MAP = {
    "satellite imagery": ("satellite", "Satellite Imagery"),
    "domestic affairs": ("domestic", "Domestic Affairs"),
    "economy": ("economy", "Economy"),
    "nuclear": ("nuclear", "Nuclear"),
    "missiles": ("missiles", "Missiles"),
    "human rights": ("humanrights", "Human Rights"),
    "china": ("china", "China & the Border"),
    "news": ("news", "News"),
    "analysis & opinion": ("analysis", "Analysis & Opinion"),
    "analysis and opinion": ("analysis", "Analysis & Opinion"),
}


def category_key(label: str) -> tuple[str, str]:
    """(key, english label) for a feed category; ("", "") when unusable.

    Keys must fit ``categories.key`` VARCHAR(32), so unknown labels are slugified
    and truncated rather than rejected - a brand-new tag must never block an
    ingest. A leading digit is prefixed (mirrors the KCNA convention).
    """
    text = _clean(html_mod.unescape(label or ""))
    if not text:
        return "", ""
    known = CATEGORY_MAP.get(text.lower())
    if known:
        return known[0], known[1]
    slug = re.sub(r"[^a-z0-9]+", "", text.lower())[:32]
    if not slug:
        return "", ""
    if slug[0].isdigit():
        slug = f"c{slug}"[:32]
    return slug, text[:64]


def category_pairs(labels: list) -> list[tuple[str, str]]:
    """Deduped (key, label) list for one item's category labels."""
    pairs: dict[str, str] = {}
    for label in labels or []:
        key, english = category_key(label)
        if key and key not in pairs:
            pairs[key] = english
    return list(pairs.items())


def tag_labels(labels: list, author: Optional[str] = None,
               max_count: int = 5) -> list[str]:
    """Trim a feed's category soup down to something the nav can carry.

    WordPress writes categories *and* free post tags into the same ``<category>``
    elements, with no ``domain`` attribute to tell them apart (verified on both
    feeds), and reporters get tagged by name - one piece came back with 15 tags,
    several of them place names for the same stretch of river. The leading labels
    are the sections an editor actually chose, so keep those, drop the ones that
    just repeat the byline, and cap the rest.
    """
    out: list[str] = []
    seen: set[str] = set()
    author_words = {w for w in re.split(r"[^a-z]+", (author or "").lower()) if len(w) > 2}
    for label in labels or []:
        text = _clean(html_mod.unescape(label or ""))
        if not text:
            continue
        lowered = text.lower()
        if author_words:
            words = {w for w in re.split(r"[^a-z]+", lowered) if len(w) > 2}
            if words and words <= author_words:     # the label *is* the byline
                continue
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(text)
        if max_count > 0 and len(out) >= max_count:
            break
    return out


# ------------------------------------------------------------------- feed parse
def _text(node, *paths: str) -> str:
    """First non-empty text among the given child paths (namespace prefixes ok)."""
    if node is None:
        return ""
    for path in paths:
        found = node.find(path, _NS)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _item_images(item, link: str) -> list[str]:
    """content:encoded first, then media:content / media:thumbnail / enclosure."""
    body = _text(item, "content:encoded", "atom:content")
    if body:
        found = images_from_html(body, link)
        if found:
            return found
    urls: list[str] = []
    for tag in ("media:content", "media:thumbnail"):
        for node in item.findall(tag, _NS):
            if node.get("url"):
                urls.append(node.get("url"))
    enclosure = item.find("enclosure", _NS)
    if enclosure is not None and (enclosure.get("type") or "").startswith("image"):
        urls.append(enclosure.get("url") or "")
    return [encode_iri(urljoin(link, u)) for u in dict.fromkeys(urls) if u]


def _labels(item, atom: bool) -> list[str]:
    """Category labels: element text in RSS, the ``term`` attribute in Atom."""
    if not atom:
        return [(c.text or "").strip() for c in item.findall("category")
                if (c.text or "").strip()]
    return [(c.get("term") or "").strip() for c in item.findall("atom:category", _NS)
            if (c.get("term") or "").strip()]


def _build_item(item, link: str, atom: bool = False) -> FeedItem:
    """Reduce one RSS ``<item>`` / Atom ``<entry>`` to a FeedItem.

    ``atom`` switches the child paths: Atom puts everything in its own namespace
    and carries dates as ``published``/``updated`` instead of ``pubDate``.
    """
    if atom:
        raw_title = _text(item, "atom:title")
        body_html = _text(item, "atom:content", "atom:summary")
        raw_summary = _text(item, "atom:summary")
        raw_date = _text(item, "atom:published", "atom:updated")
    else:
        raw_title = _text(item, "title")
        body_html = _text(item, "content:encoded")
        raw_summary = _text(item, "description")
        raw_date = _text(item, "pubDate", "dc:date")

    summary = _clean(html_to_text(raw_summary))
    published_dt = parse_datetime(raw_date)
    feed_item = FeedItem(
        id=article_id(link),
        link=link,
        title=_clean(html_mod.unescape(raw_title)),
        published_dt=published_dt,
        published=published_dt.date() if published_dt else None,
        author=_clean(_text(item, "dc:creator")) or None,
        categories=_labels(item, atom),
        excerpt=summary,
    )
    if body_html:
        cleaned = strip_chrome(body_html)
        feed_item.html_body = cleaned if _clean(cleaned) else None
        feed_item.body_text = html_to_text(body_html) or None
    else:
        feed_item.body_text = summary or None
    feed_item.image_urls = _item_images(item, link)
    return feed_item


def parse_feed(xml_bytes: bytes) -> list[FeedItem]:
    """Parse an RSS 2.0 or Atom feed into FeedItems, in feed order.

    Tolerant by design: an item with no link is dropped (nothing to fetch or
    cite), an undated item is kept and left for the caller to judge, and
    malformed XML raises so the caller can log the whole source instead of
    publishing half a feed.
    """
    if not xml_bytes:
        return []
    root = ET.fromstring(xml_bytes)
    items: list[FeedItem] = []
    seen_ids: set[str] = set()

    def add(link: str, node, atom: bool = False) -> None:
        if not link:
            return
        feed_item = _build_item(node, link, atom)
        if feed_item.id in seen_ids:
            return
        seen_ids.add(feed_item.id)
        items.append(feed_item)

    if root.tag.split("}")[-1] == "feed":                  # Atom
        for entry in root.findall("atom:entry", _NS) or root.findall("entry"):
            chosen = ""
            for node in entry.findall("atom:link", _NS) or entry.findall("link"):
                href = (node.get("href") or "").strip()
                if not href:
                    continue
                if (node.get("rel") or "alternate").lower() == "alternate":
                    chosen = href
                    break
                chosen = chosen or href
            add(chosen, entry, atom=True)
        return items

    for node in root.iter():                               # RSS 2.0 / RSS 1.0 RDF
        if node.tag.split("}")[-1] != "item":
            continue
        add(_text(node, "link"), node)
    return items

