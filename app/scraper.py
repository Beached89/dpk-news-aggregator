"""Scraping orchestration: discover -> fetch+parse -> store -> download media.

Resumable by design: every article carries a status in the DB, media downloads
are a separate idempotent pass, and a crash mid-run loses nothing.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import re
from datetime import datetime, timedelta, date as date_cls
from pathlib import Path
from urllib.parse import urlparse, unquote_to_bytes, urljoin

import httpx
from sqlalchemy import select, update, func, or_

from . import config, translate, topics
# NOTE: `align` (pulls in torch/transformers) is imported lazily inside the two
# functions that use it, so importing this module stays cheap for the web app.
from .db import Article, AgriArticle, AgriMention, AgriObservation, Category, Media, init_db, session_scope
from .kcna_client import KcnaClient
from .parser import extract_article_links, extract_categories, parse_article, ARTICLE_RE

log = logging.getLogger("kcna.scraper")

_EXT_BY_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "image/webp": ".webp", "video/mp4": ".mp4"}


def _cutoff_date() -> date_cls:
    """Oldest publication date we still mirror (now - MAX_AGE_HOURS)."""
    return (datetime.utcnow() - timedelta(hours=config.MAX_AGE_HOURS)).date()


def dedup_key(title: str | None, d) -> str | None:
    """Cross-source grouping key: normalized title + date. The same wire story
    on KCNA and Rodong shares this, so the viewer can merge them."""
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", (title or "")).lower()
    if not t:
        return None
    stamp = d.isoformat() if d else ""
    return hashlib.sha1(f"{stamp}|{t}".encode("utf-8")).hexdigest()


def recheck_skipped() -> int:
    """ONE-TIME: reopen recently-skipped articles (no-image / old, now within the
    window) so the next scrape re-evaluates them with the current logic (improved
    photo detection, 5-day window). Guarded by a sentinel file — it runs exactly
    once, then never again unless data/.recheck_done is deleted."""
    marker = config.DATA_DIR / ".recheck_done_v3"   # bump to force a one-more re-check
    if marker.exists():
        return 0
    cutoff = _cutoff_date()
    with session_scope() as s:
        res = s.execute(
            update(Article)
            .where(Article.lang == "kp",
                   Article.source.in_(["kcna", "rodong"]),
                   Article.status.in_(["skipped_no_image", "skipped_old"]),
                   or_(Article.published_date.is_(None),
                       Article.published_date >= cutoff))
            .values(status="discovered"))
        n = res.rowcount or 0
    try:
        marker.write_text(datetime.utcnow().isoformat())
    except Exception:
        pass
    log.info("recheck: reopened %d recently-skipped article(s) for re-evaluation", n)
    return n


def prune_old() -> int:
    """Hide already-stored articles that are now older than the cutoff.

    Demotes 'fetched' -> 'skipped_old' (rows are kept, just no longer shown).
    """
    cutoff = _cutoff_date()
    with session_scope() as s:
        res = s.execute(
            update(Article)
            .where(Article.status == "fetched",
                   Article.published_date.is_not(None),
                   Article.published_date < cutoff)
            .values(status="skipped_old")
        )
        n = res.rowcount or 0
    if n:
        log.info("pruned %d article(s) older than %s", n, cutoff)
    return n


# ---------------------------------------------------------------- discovery
def discover(client: KcnaClient, lang: str) -> list[str]:
    """Collect candidate article hashes from the homepage + article categories."""
    hashes: dict[str, None] = {}
    home = client.get_text(f"/{lang}")
    cat_paths = set(re.findall(rf"/{lang}/category/articles/q/[a-f0-9]+\.kcmsf", home))
    for lg, h in extract_article_links(home):
        if lg == lang:
            hashes[h] = None
    for cat in sorted(cat_paths):
        try:
            page = client.get_text(cat, referer=f"{config.BASE_URL}/{lang}")
            for lg, h in extract_article_links(page):
                if lg == lang:
                    hashes[h] = None
        except Exception as e:
            log.warning("category fetch failed %s: %s", cat, e)
    log.info("[%s] discovered %d article hashes", lang, len(hashes))
    return list(hashes)


def _register_discovered(lang: str, hashes: list[str]) -> list[str]:
    """Insert placeholder rows for hashes we haven't seen; return the new ones."""
    new: list[str] = []
    with session_scope() as s:
        for h in hashes:
            exists = s.scalar(select(Article).where(Article.hash == h, Article.lang == lang))
            if exists:
                continue
            s.add(Article(hash=h, lang=lang, url=f"{config.BASE_URL}/{lang}/article/q/{h}.kcmsf",
                          status="discovered"))
            new.append(h)
    log.info("[%s] %d newly discovered", lang, len(new))
    return new


# ---------------------------------------------------------------- categories
# KCNA's own categories, with English labels and a stable display order.
# Categories we never create/tag.
CATEGORY_SKIP = {"중요소식"}  # "Top News" — too generic

CATEGORY_EN = {
    "경애하는 김정은동지의 혁명활동소식": ("leader", "Supreme Leader's Activities", 1),
    "혁명일화": ("anecdotes", "Revolutionary Anecdotes", 2),
    "인민은 못 잊습니다": ("remember", "The People Remember", 3),
    "문건": ("documents", "Documents", 5),
    "시사해설": ("commentary", "Commentary", 6),
    "국내소식": ("domestic", "Domestic News", 7),
    "국제소식": ("international", "International News", 8),
    "대외관계": ("foreign", "Foreign Relations", 9),
    "사회생활": ("social", "Social Life", 10),
}


def _category_meta(ko_label: str) -> tuple[str, str, int]:
    if ko_label in CATEGORY_EN:
        return CATEGORY_EN[ko_label]
    key = "c" + hashlib.md5(ko_label.encode("utf-8")).hexdigest()[:10]
    return key, ko_label, 99


def discover_category_map(client: KcnaClient, lang: str) -> tuple[list, dict[str, set]]:
    """Returns (catalog, hash->category-keys). Crawls each homepage category
    listing so every article gets tagged with the categories it appears under."""
    catalog: list[tuple[str, str, str, int]] = []
    hash_cats: dict[str, set] = {}
    try:
        home = client.get_text(f"/{lang}")
    except Exception as e:
        log.warning("category discovery failed: %s", e)
        return catalog, hash_cats
    for path, ko_label in extract_categories(home):
        if ko_label in CATEGORY_SKIP:
            continue
        key, en, sort = _category_meta(ko_label)
        catalog.append((key, ko_label, en, sort))
        try:
            page = client.get_text(path, referer=f"{config.BASE_URL}/{lang}")
        except Exception:
            continue
        for lg, h in extract_article_links(page):
            if lg == lang:
                hash_cats.setdefault(h, set()).add(key)
    log.info("[%s] categories: %d, tagged hashes: %d", lang, len(catalog), len(hash_cats))
    return catalog, hash_cats


def upsert_categories(catalog: list) -> None:
    with session_scope() as s:
        for key, ko, en, sort in catalog:
            if key in config.HIDDEN_CATEGORY_KEYS:
                continue
            cat = s.scalar(select(Category).where(Category.key == key))
            if cat is None:
                s.add(Category(key=key, label_ko=ko, label_en=en, sort=sort))
            else:
                cat.label_ko, cat.label_en, cat.sort = ko, en, sort


def tag_article_categories(hash_: str, keys: set) -> None:
    keys = {k for k in (keys or set()) if k not in config.HIDDEN_CATEGORY_KEYS}
    if not keys:
        return
    with session_scope() as s:
        art = s.scalar(select(Article).where(Article.hash == hash_, Article.lang == "kp"))
        if art is None:
            return
        cats = list(s.execute(select(Category).where(Category.key.in_(list(keys)))).scalars())
        have = {c.key for c in art.categories}
        for c in cats:
            if c.key not in have:
                art.categories.append(c)


def prune_categories() -> None:
    """Delete hidden categories (and their article links) so they vanish from the
    nav and tags, even if they were created before being hidden."""
    with session_scope() as s:
        for c in s.execute(select(Category).where(
                Category.key.in_(list(config.HIDDEN_CATEGORY_KEYS)))).scalars():
            s.delete(c)


# ---------------------------------------------------------------- photos
# KCNA keeps photos in a separate section; each photo item links back to its
# article (same headline), so we can attach images to the right article.
PHOTO_ITEM_RE = re.compile(r"/(?:kp|en|cn|ru|es|jp)/media/photo/q/[a-f0-9]+\.kcmsf")
IMAGE_RE = re.compile(r"/(?:kp|en|cn|ru|es|jp)/image/q/[a-f0-9]+\.kcmsf")


def discover_photo_map(client: KcnaClient, lang: str) -> dict[str, list[str]]:
    """Map article hash -> list of image URLs, from the photo section."""
    out: dict[str, list[str]] = {}
    try:
        cat = client.get_text(f"/{lang}/category/photos.kcmsf")
    except Exception as e:
        log.warning("photo category fetch failed: %s", e)
        return out
    items = sorted(set(PHOTO_ITEM_RE.findall(cat)))
    for item in items:
        try:
            html = client.get_text(item, referer=f"{config.BASE_URL}/{lang}/category/photos.kcmsf")
        except Exception:
            continue
        arts = ARTICLE_RE.findall(html)          # (lang, hash) pairs
        imgs = list(dict.fromkeys(IMAGE_RE.findall(html)))  # dedup, keep order
        if arts and imgs:
            art_hash = arts[0][1]
            out.setdefault(art_hash, [])
            for u in imgs:
                if u not in out[art_hash]:
                    out[art_hash].append(u)
    log.info("[%s] photo map: %d articles have images", lang, len(out))
    return out


def attach_media(hash_: str, lang: str, image_urls: list[str]) -> int:
    """Attach image Media rows to a fetched article (idempotent)."""
    added = 0
    with session_scope() as s:
        art = s.scalar(select(Article).where(
            Article.hash == hash_, Article.lang == lang, Article.status == "fetched"))
        if art is None:
            return 0
        existing = {m.source_url for m in art.media}
        start = len(art.media)
        for i, url in enumerate(image_urls):
            if url in existing:
                continue
            s.add(Media(article_id=art.id, kind="image", source_url=url, position=start + i))
            added += 1
    return added


# ---------------------------------------------------------------- articles
def scrape_article(client: KcnaClient, lang: str, hash_: str) -> bool:
    path = f"/{lang}/article/q/{hash_}.kcmsf"
    try:
        html = client.get_text(path, referer=f"{config.BASE_URL}/{lang}")
    except httpx.HTTPStatusError as e:
        # 404 on an English probe = this article simply has no translation.
        # Mark it terminally 'absent' so we never re-fetch it.
        if e.response is not None and e.response.status_code == 404:
            _mark_status(lang, hash_, "absent", "no translation (404)")
        else:
            _mark_failed(lang, hash_, f"http {getattr(e.response, 'status_code', '?')}")
        return False
    except Exception as e:
        _mark_failed(lang, hash_, f"fetch: {e}")
        return False

    parsed = parse_article(html, lang, hash_)
    if not parsed.title and not parsed.body_text:
        _mark_failed(lang, hash_, "no title/body parsed")
        return False

    content_hash = hashlib.sha256((parsed.body_text or "").encode("utf-8")).hexdigest()
    cutoff = _cutoff_date()
    with session_scope() as s:
        art = s.scalar(select(Article).where(Article.hash == hash_, Article.lang == lang))
        if art is None:
            art = Article(hash=hash_, lang=lang, url=f"{config.BASE_URL}{path}")
            s.add(art)

        # Resolve publication date; English borrows its Korean sibling's date
        # (same hash) when its own page carries no parseable year.
        pub = parsed.published_date
        if pub is None:
            sib = s.scalar(select(Article).where(
                Article.hash == hash_, Article.lang != lang,
                Article.published_date.is_not(None)))
            pub = sib.published_date if sib else None

        # Recency cutoff: don't mirror anything known to be older than the window.
        if pub is not None and pub < cutoff:
            art.published_date = pub
            art.status = "skipped_old"
            art.attempts = (art.attempts or 0) + 1
            art.scraped_at = datetime.utcnow()
            log.info("[%s] skipped (old: %s) %s", lang, pub, hash_[:10])
            return False

        art.title = parsed.title
        art.subtitle = parsed.subtitle
        art.body_html = parsed.body_html
        art.body_text = parsed.body_text
        art.published_date = pub
        art.dateline = parsed.dateline
        art.content_hash = content_hash
        # Cross-source grouping is on Korean titles only (KO↔EN pair by hash).
        art.dedup_key = dedup_key(parsed.title, pub) if lang == "kp" else None
        art.status = "fetched"
        art.error = None
        art.attempts = (art.attempts or 0) + 1
        art.scraped_at = datetime.utcnow()
        s.flush()
        # register media (idempotent on (article_id, source_url))
        existing = {m.source_url for m in art.media}
        urls = [(u, "image") for u in parsed.image_urls] + [(u, "video") for u in parsed.video_urls]
        for pos, (url, kind) in enumerate(urls):
            if url in existing:
                continue
            s.add(Media(article_id=art.id, kind=kind, source_url=url, position=pos))
    log.info("[%s] fetched %s (%d imgs, %d vids) — %s",
             lang, hash_[:10], len(parsed.image_urls), len(parsed.video_urls),
             (parsed.title or "")[:48])
    return True


def _mark_status(lang: str, hash_: str, status: str, err: str | None = None) -> None:
    with session_scope() as s:
        art = s.scalar(select(Article).where(Article.hash == hash_, Article.lang == lang))
        if art is None:
            art = Article(hash=hash_, lang=lang,
                          url=f"{config.BASE_URL}/{lang}/article/q/{hash_}.kcmsf")
            s.add(art)
        art.status = status
        art.error = (err or "")[:1000]
        art.attempts = (art.attempts or 0) + 1


def _mark_failed(lang: str, hash_: str, err: str) -> None:
    _mark_status(lang, hash_, "failed", err)
    log.warning("[%s] failed %s: %s", lang, hash_[:10], err)


# ---------------------------------------------------------------- media
def _guess_ext(data: bytes, content_type: str) -> str:
    """Determine a real image/video extension. KCNA's /image endpoint sends no
    content-type, so sniff the magic bytes first and default to .jpg (never .bin,
    which browsers won't render in <img>)."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if b"ftyp" in data[:16]:
        return ".mp4"
    ct = (content_type or "").split(";")[0].strip()
    return _EXT_BY_TYPE.get(ct) or mimetypes.guess_extension(ct) or ".jpg"


def _media_filename(source_url: str, data: bytes, content_type: str, position: int) -> str:
    name = Path(urlparse(source_url).path).stem[:32] or f"media{position}"
    return f"{position:02d}_{name}{_guess_ext(data, content_type)}"


def _decode_data_uri(uri: str) -> tuple[bytes, str]:
    """Decode a `data:[<mime>][;base64],<payload>` URI to (bytes, content_type).

    Rodong inlines article photos this way, so the bytes are already in hand —
    there is nothing to fetch over HTTP."""
    header, _, payload = uri[len("data:"):].partition(",")
    ctype = header.split(";")[0].strip() or "application/octet-stream"
    if ";base64" in header.lower():
        data = base64.b64decode(payload)
    else:
        data = unquote_to_bytes(payload)
    return data, ctype


def download_pending_media(client: KcnaClient, limit: int = 500, only_hash: str | None = None) -> int:
    with session_scope() as s:
        q = select(Media).where(Media.downloaded == False)  # noqa: E712
        if only_hash is not None:
            q = q.join(Article, Media.article_id == Article.id).where(Article.hash == only_hash)
        pending = list(s.execute(q.limit(limit)).scalars())
        jobs = [(m.id, m.article_id, m.source_url, m.position) for m in pending]

    done = 0
    for media_id, article_id, source_url, position in jobs:
        with session_scope() as s:
            art = s.get(Article, article_id)
            referer = art.url if art else None
            art_hash = art.hash if art else str(article_id)
        try:
            data, ctype = client.get_bytes(source_url, referer=referer)
            sha = hashlib.sha256(data).hexdigest()
            folder = config.MEDIA_DIR / art_hash
            folder.mkdir(parents=True, exist_ok=True)
            fname = _media_filename(source_url, data, ctype, position)
            (folder / fname).write_bytes(data)
            rel = f"{art_hash}/{fname}"
            with session_scope() as s:
                m = s.get(Media, media_id)
                m.local_path = rel
                m.sha256 = sha
                m.downloaded = True
                m.error = None
            done += 1
            log.info("media saved %s (%d bytes)", rel, len(data))
        except Exception as e:
            with session_scope() as s:
                m = s.get(Media, media_id)
                m.error = str(e)[:500]
            log.warning("media failed %s: %s", source_url, e)
    return done


def ensure_assets(client: KcnaClient) -> None:
    """Download KCNA's logo once; served as the placeholder for article cards
    that have no associated photos."""
    dest = config.MEDIA_DIR / "_assets" / "kcna-logo.png"
    if dest.exists():
        return
    try:
        data, _ = client.get_bytes("/assets/img/home/mark.png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        log.info("saved KCNA logo placeholder (%d bytes)", len(data))
    except Exception as e:
        log.warning("logo fetch failed: %s", e)


def ensure_rodong_logo(client, home_html: str) -> None:
    """Save Rodong's masthead logo once, served as the card placeholder for
    articles with no photo. The logo may be an inline data: URI or a real asset."""
    from . import rodong
    from urllib.parse import urljoin
    folder = config.MEDIA_DIR / "_assets"
    if any(folder.glob("rodong-logo.*")):
        return
    src = rodong.extract_logo_src(home_html)
    if not src:
        log.warning("rodong logo not found on homepage")
        return
    try:
        if src.startswith("data:"):
            data, ctype = _decode_data_uri(src)
        else:
            url = urljoin(config.RODONG_BASE_URL + "/ko/", src)
            data, ctype = client.get_bytes(url, referer=config.RODONG_BASE_URL + "/ko/")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"rodong-logo{_guess_ext(data, ctype)}"
        dest.write_bytes(data)
        log.info("saved Rodong logo placeholder %s (%d bytes)", dest.name, len(data))
    except Exception as e:
        log.warning("rodong logo fetch failed: %s", e)


# ---------------------------------------------------------------- rodong
def _rodong_done(rid: str, lang: str, source: str = "rodong") -> bool:
    # 'skipped_*' statuses are terminal too, so we don't re-fetch them.
    with session_scope() as s:
        st = s.scalar(select(Article.status).where(
            Article.hash == rid, Article.lang == lang, Article.source == source))
        return st in ("fetched", "skipped_no_image", "skipped_no_text",
                      "skipped_old", "skipped_offtopic")


def _article_has_image(rid: str, lang: str, source: str = "rodong") -> bool:
    with session_scope() as s:
        art = s.scalar(select(Article).where(
            Article.hash == rid, Article.lang == lang, Article.source == source))
        return bool(art) and any(m.downloaded and m.kind == "image" for m in art.media)


def _article_has_english(rid: str, source: str = "rodong") -> bool:
    with session_scope() as s:
        en = s.scalar(select(Article).where(
            Article.hash == rid, Article.lang == "en", Article.source == source,
            Article.status == "fetched"))
        return bool(en and en.body_text and en.body_text.strip())


def _set_rodong_status(rid: str, lang: str, status: str, source: str = "rodong") -> None:
    with session_scope() as s:
        art = s.scalar(select(Article).where(
            Article.hash == rid, Article.lang == lang, Article.source == source))
        if art:
            art.status = status


def _translate_and_store_en(rid: str, source: str = "rodong") -> int:
    """Generate the English for a Korean article and store it as the paired
    (same-hash) lang='en' row. Translates per Korean sentence and rebuilds the
    English paragraphs to match the Korean structure, so the line-by-line view
    aligns. Returns 1 if a translation was written, 0 if skipped."""
    from .analysis import _get_kiwi
    with session_scope() as s:
        ko = s.scalar(select(Article).where(
            Article.hash == rid, Article.lang == "kp", Article.source == source))
        if ko is None:
            return 0
        existing = s.scalar(select(Article).where(
            Article.hash == rid, Article.lang == "en", Article.source == source))
        if existing is not None and existing.status == "fetched" and existing.body_text:
            return 0  # already translated
        ko_title, ko_body, ko_url, ko_date = ko.title or "", ko.body_text or "", ko.url, ko.published_date

    kiwi = _get_kiwi()
    paras = [p.strip() for p in ko_body.split("\n\n") if p.strip()]
    para_sents = []
    flat: list[str] = []
    for p in paras:
        sents = [sn.text.strip() for sn in kiwi.split_into_sents(p) if sn.text.strip()] or [p]
        para_sents.append(sents)
        flat.extend(sents)

    to_translate = ([ko_title] if ko_title else []) + flat
    translated = translate.translate_texts(to_translate)

    from . import align          # lazy: loads torch only when actually aligning
    do_align = align.ensure()
    k = 0
    en_title = None
    if ko_title:
        en_title, k = translated[0], 1
    en_paras = []
    struct = []          # paragraphs -> [{ko, en, align}] for word highlighting
    for sents in para_sents:
        para, en_sents = [], []
        for ko_s in sents:
            en_s = translated[k]
            k += 1
            en_sents.append(en_s)
            al = align.align_sentence(ko_s.split(), en_s.split()) if do_align else {}
            para.append({"ko": ko_s, "en": en_s, "align": {str(i): v for i, v in al.items()}})
        en_paras.append(" ".join(en_sents))
        struct.append(para)
    en_body = "\n\n".join(en_paras)
    align_json = json.dumps(struct, ensure_ascii=False) if do_align else None

    with session_scope() as s:
        row = s.scalar(select(Article).where(
            Article.hash == rid, Article.lang == "en", Article.source == source))
        if row is None:
            row = Article(hash=rid, lang="en")
            s.add(row)
        row.source = source
        row.url = ko_url
        row.title = en_title
        row.body_text = en_body
        row.published_date = ko_date
        row.dedup_key = None
        row.status = "fetched"
        row.scraped_at = datetime.utcnow()
        if align_json is not None:        # store alignment on the Korean row
            ko_row = s.scalar(select(Article).where(
                Article.hash == rid, Article.lang == "kp", Article.source == source))
            if ko_row is not None:
                ko_row.align_json = align_json
    return 1


def _store_rodong_row(art, full_url: str, lang: str = "kp") -> None:
    """Upsert the Rodong article row (text/title/date). Images are saved
    separately by _save_rodong_images."""
    with session_scope() as s:
        row = s.scalar(select(Article).where(Article.hash == art.rid, Article.lang == lang))
        if row is None:
            row = Article(hash=art.rid, lang=lang)
            s.add(row)
        row.source = "rodong"
        row.url = full_url
        row.title = art.title
        row.body_text = art.body_text
        row.body_html = art.body_html
        row.published_date = art.published_date
        row.dedup_key = dedup_key(art.title, art.published_date) if lang == "kp" else None
        # Hidden until the whole pipeline completes; published ("fetched") only at
        # the very end of the per-article loop so partial articles never appear.
        row.status = "processing"
        row.scraped_at = datetime.utcnow()


def _is_image_bytes(data: bytes) -> bool:
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:4] == b"GIF8" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def _save_rodong_images(rid: str, lang: str, items: list, source: str = "rodong") -> int:
    """Save decoded image bytes as Media rows + files, deduped by sha256 within
    the article. `items` = list of (data: bytes, content_type: str, source_key: str)."""
    saved = 0
    with session_scope() as s:
        row = s.scalar(select(Article).where(
            Article.hash == rid, Article.lang == lang, Article.source == source))
        if row is None:
            return 0
        seen_sha = {m.sha256 for m in row.media if m.sha256}
        seen_src = {m.source_url for m in row.media}
        pos = len(row.media)
        for data, ctype, key in items:
            if not data or len(data) < 1024 or not _is_image_bytes(data):
                continue
            sha = hashlib.sha256(data).hexdigest()
            if sha in seen_sha or key in seen_src:
                continue
            folder = config.MEDIA_DIR / rid
            folder.mkdir(parents=True, exist_ok=True)
            fname = f"{pos:02d}_img{_guess_ext(data, ctype)}"
            (folder / fname).write_bytes(data)
            s.add(Media(article_id=row.id, kind="image", source_url=key,
                        local_path=f"{rid}/{fname}", sha256=sha,
                        downloaded=True, position=pos))
            seen_sha.add(sha)
            seen_src.add(key)
            pos += 1
            saved += 1
    return saved


def _inline_items(art) -> list:
    """Fallback photo source: inline base64 (data: URI) images from the article
    HTML, decoded to (bytes, content_type, source_key)."""
    items = []
    for raw in art.image_urls:
        if not raw.startswith("data:"):
            continue
        try:
            data, ctype = _decode_data_uri(raw)
        except Exception:
            continue
        items.append((data, ctype, f"data:sha256:{hashlib.sha256(data).hexdigest()}"))
    return items


def _fetch_rodong_gallery(client, path: str, rid: str, theme_id, pub_date: str,
                          max_photos: int = 30) -> list:
    """Pull every photo behind the article's camera button via Rodong's photo API:
    POST the photo action -> parse the returned fragment's <img src> -> GET that
    full-resolution JPEG. Stops at the first missing/duplicate photo. Returns a
    list of (bytes, content_type, source_key)."""
    from . import rodong
    base = f"{config.RODONG_BASE_URL}/{path}/"
    items, seen_src = [], set()
    for n in range(1, max_photos + 1):
        body = rodong.photo_request_body(rid, theme_id, n, pub_date)
        try:
            frag = client.post_text(f"/{path}/index.php?{rodong.PHOTO_ACTION_BLOB}",
                                    data=body, referer=f"{base}index.php")
        except Exception as e:
            log.warning("rodong gallery %s photo %d POST failed: %s", rid, n, e)
            break
        src = rodong.extract_photo_img_src(frag)
        if not src or src in seen_src:   # no more photos (or wrapped around)
            break
        seen_src.add(src)
        try:
            data, ctype = client.get_bytes(f"/{path}/{src}", referer=base)
        except Exception as e:
            log.warning("rodong gallery %s photo %d GET failed: %s", rid, n, e)
            break
        if not _is_image_bytes(data):
            break
        items.append((data, ctype, f"rodong-photo:{rid}:{n}"))
    return items


def _scrape_rodong_lang(client, path: str, lang: str, limit: int, stats: dict,
                        categories: bool) -> None:
    """Scrape one Rodong language section. `path` is the URL segment (ko/en);
    `lang` is the stored language (kp/en). Links are relative to /{path}/, so
    every fetch is prefixed with it — otherwise the server 302s to the homepage."""
    from . import rodong
    base = f"{config.RODONG_BASE_URL}/{path}/"
    try:
        home = client.get_text(f"/{path}/")
    except Exception as e:
        log.warning("rodong %s homepage failed: %s", path, e)
        return

    if path == "ko":
        ensure_rodong_logo(client, home)

    href_key = {}
    if categories:
        catalog = []
        for href, ko in rodong.extract_category_links(home):
            meta = rodong.RODONG_CATEGORIES.get(ko)
            key, en, sort = meta if meta else (
                "c" + hashlib.md5(ko.encode("utf-8")).hexdigest()[:10], ko, 50)
            catalog.append((key, ko, en, sort))
            href_key[href] = key
        if catalog:
            upsert_categories(catalog)

    links: dict[str, tuple] = {}
    rid_cats: dict[str, set] = {}
    for href, rid, d in rodong.extract_article_links(home):
        links[rid] = (href, d)
    for chref, ckey in href_key.items():
        try:
            page = client.get_text(f"/{path}/{chref}", referer=base)
        except Exception:
            continue
        for href, rid, d in rodong.extract_article_links(page):
            links.setdefault(rid, (href, d))
            rid_cats.setdefault(rid, set()).add(ckey)

    # Include reopened (recheck) articles that may have dropped off the listings,
    # reconstructing their href from the stored URL.
    prefix = f"{config.RODONG_BASE_URL}/{path}/"
    with session_scope() as s:
        for rid, url in s.execute(select(Article.hash, Article.url).where(
                Article.source == "rodong", Article.lang == lang,
                Article.status == "discovered")).all():
            if rid not in links and url and url.startswith(prefix):
                links[rid] = (url[len(prefix):], None)

    # 1) Poll for new articles in the window and build the work queue.
    cutoff = _cutoff_date()
    queue: list[tuple] = []
    for rid, (href, d) in links.items():
        if d is not None and d < cutoff:
            continue                       # outside the rolling window
        if _rodong_done(rid, lang):
            if categories:                 # already done — just keep tags current
                tag_article_categories(rid, rid_cats.get(rid, set()))
            continue
        queue.append((rid, href, d))
    if limit:
        queue = queue[:limit]
    total = len(queue)
    log.info("rodong queue: %d new article(s) to process", total)

    # 2) Process each article fully (Korean -> images -> translation -> publish)
    #    before moving on, so complete bilingual articles appear one at a time.
    for i, (rid, href, d) in enumerate(queue, 1):
        # --- Korean text ---
        try:
            html = client.get_text(f"/{path}/{href}", referer=base)
        except Exception as e:
            log.warning("rodong [%d/%d] %s — korean fetch failed: %s", i, total, rid, e)
            continue
        art = rodong.parse_article(html, rid, d)
        if not (art.body_text and art.body_text.strip()):
            _store_rodong_row(art, f"{base}{href}", lang)
            _set_rodong_status(rid, lang, "skipped_no_text")
            log.info("rodong [%d/%d] %s — no text, skipped", i, total, rid)
            continue
        _store_rodong_row(art, f"{base}{href}", lang)

        # --- Topic filter: keep only self-reliance/food/agriculture/industry ---
        if config.TOPIC_FILTER and not topics.is_on_topic(f"{art.title or ''} {art.body_text or ''}"):
            _set_rodong_status(rid, lang, "skipped_offtopic")
            log.info("rodong [%d/%d] %s — off-topic, skipped", i, total, rid)
            continue

        # --- Images (gallery behind the camera button; inline lead as fallback) ---
        theme_id = rodong.theme_id_from_href(href)
        pub = art.published_date.isoformat() if art.published_date else ""
        items = _fetch_rodong_gallery(client, path, rid, theme_id, pub) or _inline_items(art)
        n_img = _save_rodong_images(rid, lang, items)
        stats["rodong_media"] += n_img
        if not _article_has_image(rid, lang):
            _set_rodong_status(rid, lang, "skipped_no_image")
            log.info("rodong [%d/%d] %s — no image, skipped", i, total, rid)
            continue

        # --- Translation + word alignment (REQUIRED before publishing) ---
        # The article stays hidden ("processing") until English exists. If it
        # can't be produced now, leave it hidden and retry on a later pass — it
        # must never appear half-finished.
        if not translate.available():
            log.warning("rodong [%d/%d] %s — held: translation unavailable", i, total, rid)
            continue
        try:
            stats["rodong_translated"] += _translate_and_store_en(rid)
        except Exception as e:
            log.warning("rodong [%d/%d] %s — held: translate failed (retry next pass): %s",
                        i, total, rid, e)
            continue
        if not _article_has_english(rid):
            log.warning("rodong [%d/%d] %s — held: no English produced", i, total, rid)
            continue

        # --- Publish: fully complete (Korean + images + English [+ alignment]) ---
        if categories:
            tag_article_categories(rid, rid_cats.get(rid, set()))
        _set_rodong_status(rid, lang, "fetched")     # now visible on the site
        stats["rodong_fetched"] += 1
        log.info("rodong [%d/%d] %s — published (%d imgs)", i, total, rid, n_img)


def scrape_rodong(limit: int) -> dict:
    """Scrape Rodong Sinmun Korean photo articles. Rodong's own English can't be
    reliably aligned to its Korean, so we scrape Korean-only (keeping articles
    that have a photo) and generate the English ourselves via translation."""
    stats = {"rodong_fetched": 0, "rodong_media": 0, "rodong_translated": 0}
    if not translate.available():
        log.warning("translation disabled — set TRANSLATE_API_KEY (provider=%s) in .env "
                    "to generate English; storing Korean-only for now", config.TRANSLATE_PROVIDER)
    client = KcnaClient(base_url=config.RODONG_BASE_URL, timeout=config.RODONG_TIMEOUT)
    try:
        client.warmup("ko")
        _scrape_rodong_lang(client, "ko", "kp", limit, stats, categories=True)
    finally:
        client.close()
    # Safety net: translation now happens inline per article, but this retries
    # any that failed inline (transient errors) or predate a translation key.
    stats["rodong_translated"] += backfill_rodong_translations(max_items=limit)
    # Backfill word alignment onto already-translated articles that lack it
    # (e.g., translated before the alignment model was working).
    stats["rodong_aligned"] = backfill_rodong_alignment(max_items=limit)
    log.info("rodong: %s", stats)
    return stats


def backfill_rodong_alignment(max_items: int | None = None) -> int:
    """Compute word alignment for already-translated Rodong articles that don't
    have it yet (reuses the stored English — no re-scrape or re-translate).
    Pairs KO sentences (kiwi) to EN sentences (regex) per paragraph; only aligns
    when the counts match, else stores that paragraph unaligned. Returns count."""
    from . import align          # lazy: loads torch only when actually aligning
    if not align.ensure():
        return 0
    from .analysis import _get_kiwi, _split_en_sentences
    kiwi = _get_kiwi()
    with session_scope() as s:
        todo = s.execute(select(Article.hash).where(
            Article.source == "rodong", Article.lang == "kp", Article.status == "fetched",
            Article.align_json.is_(None))).scalars().all()
    if max_items:
        todo = todo[:max_items]
    n = 0
    for rid in todo:
        with session_scope() as s:
            ko = s.scalar(select(Article).where(
                Article.hash == rid, Article.lang == "kp", Article.source == "rodong"))
            en = s.scalar(select(Article).where(
                Article.hash == rid, Article.lang == "en", Article.source == "rodong",
                Article.status == "fetched"))
            if not ko or not en or not (en.body_text and en.body_text.strip()):
                continue
            ko_body, en_body = ko.body_text or "", en.body_text or ""
        ko_paras = [p for p in ko_body.split("\n\n") if p.strip()]
        en_paras = [p for p in en_body.split("\n\n") if p.strip()]
        struct = []
        for i, kp in enumerate(ko_paras):
            ep = en_paras[i] if i < len(en_paras) else ""
            ko_sents = [x.text.strip() for x in kiwi.split_into_sents(kp) if x.text.strip()] or [kp]
            en_sents = _split_en_sentences(ep) if ep else []
            para = []
            if en_sents and len(ko_sents) == len(en_sents):
                for ks, es in zip(ko_sents, en_sents):
                    al = align.align_sentence(ks.split(), es.split())
                    para.append({"ko": ks, "en": es, "align": {str(k): v for k, v in al.items()}})
            else:                       # counts differ -> keep paragraph unaligned
                para.append({"ko": kp, "en": ep, "align": {}})
            struct.append(para)
        with session_scope() as s:
            ko = s.scalar(select(Article).where(
                Article.hash == rid, Article.lang == "kp", Article.source == "rodong"))
            if ko is not None:
                ko.align_json = json.dumps(struct, ensure_ascii=False)
        n += 1
    if n:
        log.info("rodong alignment backfilled: %d", n)
    return n


def backfill_rodong_translations(max_items: int | None = None) -> int:
    """Translate KO->EN for shown Rodong articles that still lack an English row.
    Idempotent (skips already-translated). Returns the number translated."""
    if not translate.available():
        return 0
    with session_scope() as s:
        kp = set(s.execute(select(Article.hash).where(
            Article.source == "rodong", Article.lang == "kp", Article.status == "fetched",
            func.length(func.coalesce(Article.body_text, "")) > 0)).scalars().all())
        en = set(s.execute(select(Article.hash).where(
            Article.source == "rodong", Article.lang == "en", Article.status == "fetched",
            func.length(func.coalesce(Article.body_text, "")) > 0)).scalars().all())
    todo = sorted(kp - en, reverse=True)
    if max_items:
        todo = todo[:max_items]
    n = 0
    for rid in todo:
        try:
            n += _translate_and_store_en(rid)
        except Exception as e:
            log.warning("rodong translate %s failed: %s", rid, e)
    if n:
        log.info("rodong translations backfilled: %d", n)
    return n


def backfill_dedup() -> None:
    """Compute dedup_key for older Korean rows scraped before the feature."""
    with session_scope() as s:
        rows = list(s.execute(select(Article).where(
            Article.lang == "kp", Article.status == "fetched", Article.dedup_key.is_(None))).scalars())
        for a in rows:
            a.dedup_key = dedup_key(a.title, a.published_date)


def tag_juche() -> int:
    """Content-classify Korean articles into the 'Juche' (self-reliance) topic."""
    from .topics import classify_juche
    upsert_categories([("juche", "자력갱생", "Juche · Self-Reliance", 16)])
    tagged = 0
    with session_scope() as s:
        juche = s.scalar(select(Category).where(Category.key == "juche"))
        arts = list(s.execute(select(Article).where(
            Article.lang == "kp", Article.status == "fetched")).scalars())
        for a in arts:
            if any(c.key == "juche" for c in a.categories):
                continue
            if classify_juche(f"{a.title or ''} {a.body_text or ''}"):
                a.categories.append(juche)
                tagged += 1
    return tagged


# ---------------------------------------------------------------- run
def _pending_langs(hash_: str, langs: list[str]) -> list[str]:
    """Languages (in priority order) in which this hash still needs scraping."""
    with session_scope() as s:
        rows = dict(s.execute(
            select(Article.lang, Article.status).where(Article.hash == hash_)
        ).all())
    return [lg for lg in langs if rows.get(lg) in ("discovered", "failed")]


def _is_fetched(hash_: str, lang: str) -> bool:
    with session_scope() as s:
        return s.scalar(select(Article.status).where(
            Article.hash == hash_, Article.lang == lang)) == "fetched"


def attach_photos(client: KcnaClient, photo_map: dict, langs: list[str]) -> int:
    """Scrape (if needed) each photo set's article and attach + download images.

    Runs independently of the per-run article limit so photos are never starved
    by the discovery backlog.
    """
    media = 0
    for art_hash, urls in photo_map.items():
        if not _is_fetched(art_hash, "kp"):
            scrape_article(client, "kp", art_hash)          # respects MAX_AGE_HOURS cutoff
            if "en" in langs and not _is_fetched(art_hash, "en"):
                scrape_article(client, "en", art_hash)
        if _is_fetched(art_hash, "kp") and attach_media(art_hash, "kp", urls):
            media += download_pending_media(client, only_hash=art_hash)
    return media


def run_once(langs: list[str] | None = None, limit: int | None = None,
             sources: set[str] | None = None, maintenance: bool = True) -> dict:
    """Run one scraping pass.

    `sources` selects which feeds to scrape this process — a subset of
    {"kcna", "rodong", "vok"}. When None, it falls back to the per-site
    SCRAPE_* config flags (single-container back-compat). `maintenance` runs the
    cross-source upkeep (recency prune, Juche tagging, dedup, category prune);
    with the feeds split across containers, exactly one container should own it.
    """
    init_db()
    langs = langs or config.LANGS
    limit = limit or config.MAX_ARTICLES_PER_RUN
    if sources is None:
        sources = set()
        if config.SCRAPE_KCNA:
            sources.add("kcna")
        if config.SCRAPE_RODONG:
            sources.add("rodong")
        if config.SCRAPE_VOK:
            sources.add("vok")
        if config.SCRAPE_MAGAZINES:
            sources.add("magazine")

    stats: dict = {"discovered": 0, "fetched": 0, "media": 0}
    if maintenance:
        recheck_skipped()          # one-time reopen of recent skips (sentinel-guarded)
        stats["pruned"] = prune_old()
    if "kcna" in sources:
        try:
            stats.update(scrape_kcna(limit))
        except Exception as e:
            log.warning("kcna pass failed: %s", e)
    if "rodong" in sources and "kp" in langs:
        try:
            stats.update(scrape_rodong(limit))
        except Exception as e:
            log.warning("rodong pass failed: %s", e)
    if "vok" in sources:
        try:
            stats.update(scrape_vok(limit))
        except Exception as e:
            log.warning("vok pass failed: %s", e)
    if "magazine" in sources:
        try:
            stats.update(scrape_magazines(limit))
        except Exception as e:
            log.warning("magazine pass failed: %s", e)
    if "agriculture" in sources:
        try:
            stats.update(scrape_agriculture(limit))
        except Exception as e:
            log.warning("agriculture pass failed: %s", e)
    if maintenance:
        stats["juche"] = tag_juche()
        backfill_dedup()
        prune_categories()
    log.info("run complete (sources=%s, maintenance=%s): %s",
             sorted(sources), maintenance, stats)
    return stats


def _store_kcna_ko_row(parsed, h: str) -> None:
    """Upsert the KCNA Korean row as 'processing' (hidden) until the whole
    pipeline completes — mirrors _store_rodong_row."""
    with session_scope() as s:
        row = s.scalar(select(Article).where(Article.hash == h, Article.lang == "kp"))
        if row is None:
            row = Article(hash=h, lang="kp")
            s.add(row)
        row.source = "kcna"
        row.url = f"{config.BASE_URL}/kp/article/q/{h}.kcmsf"
        row.title = parsed.title
        row.subtitle = parsed.subtitle
        row.body_text = parsed.body_text
        row.body_html = parsed.body_html
        row.published_date = parsed.published_date
        row.dateline = parsed.dateline
        row.dedup_key = dedup_key(parsed.title, parsed.published_date)
        row.status = "processing"
        row.scraped_at = datetime.utcnow()


# A KCNA article links its photos via <div class="media-icon"><a href="/kp/
# media/photo/q/<hash>.kcmsf"> (a per-article photo page). That page lists the
# actual /kp/image/q/<hash>.kcmsf images. So: find the photo-page link, fetch
# it, and collect the image URLs.
_PHOTO_PAGE_RE = re.compile(r"/(?:kp|en|cn|ru|es|jp)/media/photo/q/[a-f0-9]+\.kcmsf")


def _kcna_photo_urls(client, h: str, html: str, arturl: str, photo_map: dict) -> list[str]:
    """Resolve a KCNA article's image URLs from its per-article photo page (the
    .media-icon camera link), plus any inline images and the photo-section map."""
    imgs: list[str] = []

    def add(u: str) -> None:
        if u and u not in imgs:
            imgs.append(u)

    # 1) any image URLs inline in the article HTML (rare).
    for u in IMAGE_RE.findall(html):
        add(u)
    # 2) the per-article photo page(s) linked from the .media-icon camera button.
    for pp in dict.fromkeys(_PHOTO_PAGE_RE.findall(html)):
        try:
            frag = client.get_text(pp, referer=arturl)
            for u in IMAGE_RE.findall(frag):
                add(u)
        except Exception as e:
            log.warning("kcna photo page %s failed: %s", pp, e)
    # 3) the separate photo-section map (best-effort reverse lookup).
    for u in photo_map.get(h, []):
        add(u)
    return imgs


def _scrape_kcna(client, limit: int, stats: dict) -> None:
    """KCNA, emulating the Rodong pipeline: Korean photo articles only, English
    generated by us (per-sentence) and word-aligned, each published only when
    fully complete, one at a time. KCNA's own English is intentionally unused —
    it isn't sentence-aligned to the Korean, which the learning view needs."""
    client.warmup("kp")
    # Poll: Korean article hashes + categories + the separate photo map.
    catalog, hash_cats = discover_category_map(client, "kp")
    upsert_categories(catalog)
    hashes = discover(client, "kp")
    photo_map = discover_photo_map(client, "kp")

    # Include reopened (recheck) articles that may have dropped off the listings.
    with session_scope() as s:
        reopened = list(s.execute(select(Article.hash).where(
            Article.source == "kcna", Article.lang == "kp",
            Article.status == "discovered")).scalars())

    order, seen = [], set()
    for h in list(hashes) + list(hash_cats.keys()) + reopened:
        if h in seen:
            continue
        seen.add(h)
        if _rodong_done(h, "kp", "kcna"):
            tag_article_categories(h, hash_cats.get(h, set()))
            continue
        order.append(h)
    if limit:
        order = order[:limit]
    total = len(order)
    log.info("kcna queue: %d new article(s) to process", total)

    cutoff = _cutoff_date()
    for i, h in enumerate(order, 1):
        arturl = f"{config.BASE_URL}/kp/article/q/{h}.kcmsf"
        try:
            html = client.get_text(f"/kp/article/q/{h}.kcmsf", referer=f"{config.BASE_URL}/kp")
        except Exception as e:
            log.warning("kcna [%d/%d] %s — korean fetch failed: %s", i, total, h[:10], e)
            continue
        art = parse_article(html, "kp", h)
        if art.published_date and art.published_date < cutoff:
            _store_kcna_ko_row(art, h)
            _set_rodong_status(h, "kp", "skipped_old", "kcna")
            continue
        if not (art.body_text and art.body_text.strip()):
            _store_kcna_ko_row(art, h)
            _set_rodong_status(h, "kp", "skipped_no_text", "kcna")
            log.info("kcna [%d/%d] %s — no text, skipped", i, total, h[:10])
            continue
        _store_kcna_ko_row(art, h)

        # Topic filter: keep only self-reliance/food/agriculture/industry stories.
        if config.TOPIC_FILTER and not topics.is_on_topic(f"{art.title or ''} {art.body_text or ''}"):
            _set_rodong_status(h, "kp", "skipped_offtopic", "kcna")
            log.info("kcna [%d/%d] %s — off-topic, skipped", i, total, h[:10])
            continue

        # Images: KCNA exposes photos via <a> links / a photo page (not <img>),
        # so resolve them from the article HTML + the photo-section map.
        urls = list(dict.fromkeys(
            list(art.image_urls) + _kcna_photo_urls(client, h, html, arturl, photo_map)))
        items = []
        for u in urls:
            try:
                data, ctype = client.get_bytes(u, referer=arturl)
                items.append((data, ctype, u))
            except Exception as e:
                log.warning("kcna image %s failed: %s", u, e)
        stats["kcna_media"] += _save_rodong_images(h, "kp", items, source="kcna")
        if not _article_has_image(h, "kp", "kcna"):
            _set_rodong_status(h, "kp", "skipped_no_image", "kcna")
            log.info("kcna [%d/%d] %s — no image, skipped", i, total, h[:10])
            continue

        # Translation + alignment (required before publishing).
        if not translate.available():
            log.warning("kcna [%d/%d] %s — held: translation unavailable", i, total, h[:10])
            continue
        try:
            stats["kcna_translated"] += _translate_and_store_en(h, source="kcna")
        except Exception as e:
            log.warning("kcna [%d/%d] %s — held: translate failed (retry next pass): %s",
                        i, total, h[:10], e)
            continue
        if not _article_has_english(h, "kcna"):
            log.warning("kcna [%d/%d] %s — held: no English produced", i, total, h[:10])
            continue

        # Publish: fully complete (Korean + images + English [+ alignment]).
        tag_article_categories(h, hash_cats.get(h, set()))
        _set_rodong_status(h, "kp", "fetched", "kcna")
        stats["kcna_fetched"] += 1
        log.info("kcna [%d/%d] %s — published", i, total, h[:10])


def scrape_kcna(limit: int) -> dict:
    """KCNA pass — Korean photo articles, self-translated + aligned (mirrors Rodong)."""
    stats = {"kcna_fetched": 0, "kcna_media": 0, "kcna_translated": 0}
    if not translate.available():
        log.warning("translation disabled — KCNA stored Korean-only until a translator is set")
    with KcnaClient() as client:
        _scrape_kcna(client, limit, stats)
    log.info("kcna: %s", stats)
    return stats


# ---------------------------------------------------------------- vok (video)
def _vtt_ts(t: float) -> str:
    """Seconds -> WebVTT timestamp (HH:MM:SS.mmm)."""
    if t is None or t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _segments_to_vtt(segments: list[dict], key: str) -> str:
    """Render timed segments as a WebVTT subtitle track for the given text key
    ("text" for Korean, "en" for English)."""
    lines = ["WEBVTT", ""]
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_vtt_ts(seg.get('start', 0))} --> {_vtt_ts(seg.get('end', 0))}")
        lines.append((seg.get(key) or "").strip())
        lines.append("")
    return "\n".join(lines)


def _vok_done(vid: str) -> bool:
    with session_scope() as s:
        st = s.scalar(select(Article.status).where(
            Article.hash == vid, Article.lang == "kp", Article.source == "vok"))
        return st in ("fetched", "skipped_no_audio")


def _ffmpeg_download(m3u8_url: str, out_path: Path) -> bool:
    """Download an HLS stream to a local .mp4 via ffmpeg (stream copy, no
    re-encode). Returns True on success."""
    import subprocess
    headers = (f"Referer: {config.VOK_BASE_URL}/\r\n"
               f"User-Agent: {config.USER_AGENT}\r\n")
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-headers", headers,
           "-i", m3u8_url,
           "-c", "copy", "-bsf:a", "aac_adtstoasc",
           str(out_path)]
    try:
        r = subprocess.run(cmd, capture_output=True,
                           timeout=config.VOK_DOWNLOAD_TIMEOUT)
    except FileNotFoundError:
        log.warning("vok: ffmpeg not installed — cannot download video")
        return False
    except Exception as e:
        log.warning("vok: ffmpeg download error: %s", e)
        return False
    if r.returncode != 0:
        log.warning("vok: ffmpeg failed: %s", (r.stderr or b"").decode("utf-8", "ignore")[:300])
        return False
    return out_path.exists() and out_path.stat().st_size > 0


def _ffmpeg_thumbnail(mp4: Path, out_path: Path, ts: float = 4.0) -> bool:
    """Grab a single representative frame from the video as a JPEG poster."""
    import subprocess
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(ts), "-i", str(mp4),
           "-frames:v", "1", "-q:v", "3", "-vf", "scale=640:-1", str(out_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    except Exception as e:
        log.warning("vok: thumbnail error: %s", e)
        return False
    return r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def _store_vok_rows(vid: str, ko_title: str, en_title: str, segments: list[dict],
                    folder: Path, status: str) -> None:
    """Upsert the VOK video as an Article pair (KO transcript + EN transcript)
    plus a video Media row. The timed segments (with EN) live in align_json on
    the Korean row, driving the synced transcript view."""
    detail = f"{config.VOK_BASE_URL}/index.php/detail_com/vi_video/{vid}"
    ko_text = "\n".join(s["text"] for s in segments)
    en_text = "\n".join((s.get("en") or "") for s in segments)
    seg_json = json.dumps(
        [{"start": s.get("start", 0), "end": s.get("end", 0),
          "ko": s.get("text", ""), "en": s.get("en", "")} for s in segments],
        ensure_ascii=False) if segments else None
    rel_mp4 = f"vok_{vid}/video.mp4"
    has_mp4 = (folder / "video.mp4").exists()
    rel_poster = f"vok_{vid}/poster.jpg"
    has_poster = (folder / "poster.jpg").exists()
    with session_scope() as s:
        ko = s.scalar(select(Article).where(
            Article.hash == vid, Article.lang == "kp", Article.source == "vok"))
        if ko is None:
            ko = Article(hash=vid, lang="kp")
            s.add(ko)
        ko.source = "vok"
        ko.url = detail
        ko.title = ko_title
        ko.body_text = ko_text
        ko.align_json = seg_json
        ko.published_date = None          # curated feed: never auto-pruned by age
        ko.dedup_key = None
        ko.status = status
        ko.scraped_at = datetime.utcnow()
        s.flush()
        if has_mp4 and not any(m.kind == "video" for m in ko.media):
            s.add(Media(article_id=ko.id, kind="video", source_url=f"vok:{vid}",
                        local_path=rel_mp4, downloaded=True, position=0))
        # Poster frame (kind=image) — used as the card/sidebar thumbnail and the
        # <video> poster.
        if has_poster and not any(m.kind == "image" for m in ko.media):
            s.add(Media(article_id=ko.id, kind="image", source_url=f"vok-poster:{vid}",
                        local_path=rel_poster, downloaded=True, position=1))
        if status == "fetched" and segments:
            en = s.scalar(select(Article).where(
                Article.hash == vid, Article.lang == "en", Article.source == "vok"))
            if en is None:
                en = Article(hash=vid, lang="en")
                s.add(en)
            en.source = "vok"
            en.url = detail
            en.title = en_title
            en.body_text = en_text
            en.published_date = None
            en.status = "fetched"
            en.scraped_at = datetime.utcnow()


def _process_vok_video(client, vid: str, stats: dict) -> None:
    """Full per-video pipeline: player page -> tokenized m3u8 -> ffmpeg download
    -> Whisper transcribe -> translate -> WebVTT -> publish. Held (not published)
    if any step can't complete, so a video never appears without subtitles."""
    from . import vok, transcribe
    html = client.get_text(vok.player_path(vid))
    m3u8 = vok.extract_m3u8_url(html)
    if not m3u8:
        log.warning("vok %s — no stream URL in player page (held)", vid)
        return
    # Korean title: curated (config) first; else the player page's title/ID.
    ko_title = config.VOK_TITLES.get(vid) or vok.extract_title(html, vid)

    folder = config.MEDIA_DIR / f"vok_{vid}"
    folder.mkdir(parents=True, exist_ok=True)
    mp4 = folder / "video.mp4"
    if not (mp4.exists() and mp4.stat().st_size > 0):
        if not _ffmpeg_download(m3u8, mp4):
            log.warning("vok %s — download failed (held)", vid)
            return
    log.info("vok %s — downloaded video (%d bytes), transcribing…", vid, mp4.stat().st_size)

    # Poster frame from the video itself (used as the thumbnail everywhere).
    poster = folder / "poster.jpg"
    if not poster.exists():
        _ffmpeg_thumbnail(mp4, poster)

    # English name: machine-translate the Korean title (best-effort).
    en_title = ko_title
    if translate.available() and any('가' <= c <= '힣' for c in ko_title):
        try:
            en_title = translate.translate_texts([ko_title])[0] or ko_title
        except Exception:
            en_title = ko_title

    segments = transcribe.transcribe_korean(mp4)
    if not segments:
        # Could be a held state (model missing) or genuinely no speech. If the
        # model is unavailable, leave it unpublished to retry; otherwise mark it.
        if not transcribe.ensure():
            log.warning("vok %s — held: transcription unavailable", vid)
            return
        _store_vok_rows(vid, ko_title, en_title, [], folder, status="skipped_no_audio")
        log.info("vok %s — no speech detected, skipped", vid)
        return

    if not translate.available():
        log.warning("vok %s — held: translation unavailable", vid)
        return
    try:
        en_texts = translate.translate_texts([s["text"] for s in segments])
    except Exception as e:
        log.warning("vok %s — held: translate failed (retry next pass): %s", vid, e)
        return
    for seg, en in zip(segments, en_texts):
        seg["en"] = en

    (folder / "ko.vtt").write_text(_segments_to_vtt(segments, "text"), encoding="utf-8")
    (folder / "en.vtt").write_text(_segments_to_vtt(segments, "en"), encoding="utf-8")

    _store_vok_rows(vid, ko_title, en_title, segments, folder, status="fetched")
    stats["vok_fetched"] += 1
    log.info("vok %s — published (%d subtitle segments)", vid, len(segments))


def backfill_vok_vocab(max_items: int | None = None) -> int:
    """Build the per-video 'major words' vocabulary list (media/vok_<id>/vocab.json)
    for any published video that lacks it. Uses the offline dictionary first and
    machine-translates the remaining content words once. Returns count built."""
    if not translate.available():
        return 0
    from .analysis import major_words, dict_meaning
    with session_scope() as s:
        vids = s.execute(select(Article.hash, Article.align_json).where(
            Article.source == "vok", Article.lang == "kp", Article.status == "fetched")).all()
    n = 0
    for vid, raw in vids:
        if not raw:
            continue
        folder = config.MEDIA_DIR / f"vok_{vid}"
        vpath = folder / "keywords.json"
        # Reuse an existing list (so we only translate what's still missing);
        # otherwise build the full word list from the transcript.
        vocab = None
        if vpath.exists():
            try:
                vocab = json.loads(vpath.read_text(encoding="utf-8"))
            except Exception:
                vocab = None
        if vocab is None:
            try:
                segs = json.loads(raw)
            except Exception:
                continue
            words = major_words([seg.get("ko", "") for seg in segs])
            vocab = [{"ko": w["ko"], "en": dict_meaning(w["form"], w["tag"]) or ""}
                     for w in words]
        # Fill in any entries that still lack an English gloss.
        need_idx = [i for i, v in enumerate(vocab) if not v.get("en")]
        if not need_idx and vpath.exists():
            continue                              # already complete
        if need_idx:
            try:
                tr = translate.translate_texts([vocab[i]["ko"] for i in need_idx])
            except Exception as e:
                log.warning("vok %s vocab translate failed: %s", vid, e)
                tr = None
            if tr:
                for i, t in zip(need_idx, tr):
                    # "—" marks a word we tried but couldn't translate, so it's
                    # kept in the list and not retried every pass.
                    vocab[i]["en"] = t if (t and t.strip() and t != vocab[i]["ko"]) else "—"
        folder.mkdir(parents=True, exist_ok=True)
        vpath.write_text(json.dumps(vocab, ensure_ascii=False), encoding="utf-8")
        n += 1
        log.info("vok vocab built %s (%d words)", vid, len(vocab))
        if max_items and n >= max_items:
            break
    return n


# ---------------------------------------------------------------- agriculture
_NOT_FOUND_MARKERS = (
    "article was not found", "article not found", "page not found",
    "not found", "찾을 수 없", "페지를 찾", "페이지를 찾",
)


def _looks_not_found(text: str | None) -> bool:
    """True if a short scraped string is actually a 'not found' placeholder page
    (KCNA serves these for hashes that have no edition in the requested language)."""
    if not text:
        return False
    t = text.strip().lower()
    return len(t) < 200 and any(m in t for m in _NOT_FOUND_MARKERS)


def _store_agri(source, h, ko_title, en_title, ko_body, en_body, url, pub) -> int:
    with session_scope() as s:
        row = s.scalar(select(AgriArticle).where(
            AgriArticle.source == source, AgriArticle.hash == h))
        if row is None:
            row = AgriArticle(source=source, hash=h)
            s.add(row)
        row.ko_title, row.en_title = ko_title, en_title
        row.ko_body, row.en_body = ko_body, en_body
        row.url, row.published_date = url, pub
        row.scraped_at = datetime.utcnow()
        s.flush()
        return row.id


def _mt_body(text: str) -> str | None:
    """Machine-translate an article body KO->EN, paragraph by paragraph, so the
    Agriculture reader is bilingual even for Rodong (KO-only) articles and for
    KCNA articles whose English edition couldn't be fetched. Best-effort: returns
    None if there's nothing to translate or the translator is unavailable."""
    if not (text and text.strip()) or not translate.available():
        return None
    paras = [p for p in text.split("\n\n") if p.strip()]
    if not paras:
        return None
    try:
        en = translate.translate_texts(paras)
    except Exception as e:
        log.warning("agri body translate failed: %s", e)
        return None
    return "\n\n".join(en)


def _extract_agri(article_id: int, ko_text: str, en_text: str = "") -> tuple[int, int]:
    """Re-extract quantified facts AND commodity mentions for an article
    (idempotent: replaces prior rows) so the analysis stays live as articles are
    re-scraped. Quantities come from the Korean original (authoritative units);
    mentions come from both languages so every foodstuff discussed is listed,
    even with no number attached. Returns (n_facts, n_mentions)."""
    from . import agri_extract, agri_ai, agri_places
    # Extract quantities + place names from ONE text so positions line up (prefer
    # the English translation: it carries both the numbers and the place names in
    # comparable coordinates). Each fact with a tonnage becomes a place-tagged
    # 'rules' observation; manual/AI observations are left untouched.
    place_text = en_text or ko_text or ""
    positions = agri_places.place_positions(place_text)
    items = []
    for f in agri_extract.extract_facts(place_text):
        if f.get("tonnes") is None:          # counts/no-tonnage aren't production
            continue
        place = agri_places.nearest_place(f.get("pos", 0), positions) if positions else None
        items.append({"commodity": f["commodity"], "place": place,
                      "low": f["tonnes"], "high": f["tonnes"],
                      "is_estimate": f["is_estimate"], "note": f["phrase"]})
    agri_ai.record_observations(article_id, items, kind="rules")

    # Mentions (every foodstuff named) come from BOTH languages.
    mentions = agri_extract.extract_mentions(ko_text or "", en_text or "")
    with session_scope() as s:
        for old in s.execute(select(AgriMention).where(AgriMention.article_id == article_id)).scalars():
            s.delete(old)
        s.flush()        # run the DELETEs before the INSERTs so the (article,
                         # commodity) unique key doesn't collide with old rows
        for key, cnt in mentions.items():
            s.add(AgriMention(article_id=article_id, commodity=key, count=cnt))
    return len(items), len(mentions)


def ingest_url(url: str) -> dict:
    """Fetch ONE pasted article URL, parse + translate + store it as an
    AgriArticle, then extract quantities/mentions. Bypasses the topic filter (the
    user explicitly chose this article). Returns {'id', 'title'} or raises
    ValueError on an unrecognized/unfetchable URL. Works best with KCNA article
    links (kcna.kp/.../article/q/<hash>.kcmsf); also handles Rodong links."""
    init_db()
    url = (url or "").strip()
    if not url:
        raise ValueError("Please paste an article URL.")

    m = re.search(r"/(?:kp|en|cn|ru|es|jp)/article/q/([a-f0-9]+)\.kcmsf", url)
    if m or "kcna" in url.lower():
        if not m:
            raise ValueError("Couldn't find a KCNA article id in that URL.")
        h = m.group(1)
        with KcnaClient() as client:
            client.warmup("kp")
            art = parse_article(
                client.get_text(f"/kp/article/q/{h}.kcmsf", referer=f"{config.BASE_URL}/kp"),
                "kp", h)
            if not (art.body_text and art.body_text.strip()):
                raise ValueError("That KCNA page had no article text.")
            en_title = en_body = None
            try:
                en = parse_article(
                    client.get_text(f"/en/article/q/{h}.kcmsf", referer=f"{config.BASE_URL}/en"),
                    "en", h)
                en_title, en_body = en.title, en.body_text
                if _looks_not_found(en_title):
                    en_title = None
                if _looks_not_found(en_body):
                    en_body = None
            except Exception:
                pass
            if not en_title and translate.available() and art.title:
                try:
                    en_title = translate.translate_texts([art.title])[0]
                except Exception:
                    pass
            if not (en_body and en_body.strip()):
                en_body = _mt_body(art.body_text)
            aid = _store_agri("kcna", h, art.title, en_title, art.body_text, en_body,
                              f"{config.BASE_URL}/kp/article/q/{h}.kcmsf", art.published_date)
            _extract_agri(aid, art.body_text, en_body or "")
            return {"id": aid, "title": en_title or art.title or h}

    if "rodong" in url.lower():
        from . import rodong
        rid = "url-" + hashlib.sha1(url.encode()).hexdigest()[:16]
        client = KcnaClient(base_url=config.RODONG_BASE_URL, timeout=config.RODONG_TIMEOUT)
        try:
            path = url.split(".kp", 1)[-1] or "/"
            art = rodong.parse_article(client.get_text(path, referer=f"{config.RODONG_BASE_URL}/ko/"),
                                       rid, None)
        finally:
            client.close()
        if not (art.body_text and art.body_text.strip()):
            raise ValueError("Couldn't parse article text from that Rodong URL.")
        en_title = None
        if translate.available() and art.title:
            try:
                en_title = translate.translate_texts([art.title])[0]
            except Exception:
                pass
        en_body = _mt_body(art.body_text)
        aid = _store_agri("rodong", rid, art.title, en_title, art.body_text, en_body,
                          url, art.published_date)
        _extract_agri(aid, art.body_text, en_body or "")
        return {"id": aid, "title": en_title or art.title or rid}

    raise ValueError("Unrecognized link — paste a KCNA (kcna.kp) or Rodong (rodong.rep.kp) article URL.")


def _agri_cleanup() -> dict:
    """Self-healing maintenance over the stored Agriculture archive:
      * delete rows that no longer pass the (tightened) is_agriculture filter —
        purges false positives captured under earlier, looser keyword rules;
      * backfill a machine-translated English body for any row missing one, then
        re-extract its quantity facts.
    Runs at the start of every agriculture pass so the archive stays correct and
    bilingual as the rules evolve — no manual DB purge needed."""
    removed = backfilled = fixed_titles = 0
    with session_scope() as s:
        rows = list(s.execute(select(AgriArticle)).scalars())
    for a in rows:
        blob = f"{a.ko_title or ''} {a.ko_body or ''}"
        if not topics.is_agriculture(blob):
            with session_scope() as s:
                for o in s.execute(select(AgriObservation).where(AgriObservation.article_id == a.id)).scalars():
                    s.delete(o)
                for m in s.execute(select(AgriMention).where(AgriMention.article_id == a.id)).scalars():
                    s.delete(m)
                row = s.get(AgriArticle, a.id)
                if row is not None:
                    s.delete(row)
            removed += 1
            continue
        # Repair a bad English TITLE left by a KCNA "article not found" page (or a
        # missing one) by translating the good Korean title.
        if (_looks_not_found(a.en_title) or not (a.en_title and a.en_title.strip())) \
                and a.ko_title and translate.available():
            try:
                new_t = translate.translate_texts([a.ko_title])[0]
            except Exception:
                new_t = None
            if new_t and not _looks_not_found(new_t):
                with session_scope() as s:
                    row = s.get(AgriArticle, a.id)
                    if row is not None:
                        row.en_title = new_t
                fixed_titles += 1

        # Keep: ensure an English body exists (backfill if missing or it's a
        # not-found placeholder), then always (re)extract facts + mentions.
        en_body = a.en_body
        if (not (en_body and en_body.strip()) or _looks_not_found(en_body)) \
                and (a.ko_body and a.ko_body.strip()):
            en_body = _mt_body(a.ko_body)
            if en_body:
                with session_scope() as s:
                    row = s.get(AgriArticle, a.id)
                    if row is not None:
                        row.en_body = en_body
                backfilled += 1
        _extract_agri(a.id, a.ko_body or "", en_body or "")
    if removed or backfilled or fixed_titles:
        log.info("agri cleanup: removed %d off-topic, backfilled %d bodies, fixed %d titles",
                 removed, backfilled, fixed_titles)
    return {"agri_removed": removed, "agri_backfilled": backfilled, "agri_titles_fixed": fixed_titles}


def scrape_agriculture(limit: int | None = None) -> dict:
    """One-time topical scrape: scan KCNA + Rodong (KO) articles and keep any
    that mention food / farming / agriculture / foodstuffs — regardless of
    photos. Captures KO + EN title/body (KCNA EN by same hash; Rodong title
    machine-translated) for the Agriculture page + later analysis."""
    from . import rodong
    stats = {"agri_scanned": 0, "agri_kept": 0}
    stats.update(_agri_cleanup())   # purge stale false-positives + backfill EN

    # ---- KCNA: Korean article + the English version at the same hash ----
    try:
        with KcnaClient() as client:
            client.warmup("kp")
            for h in discover(client, "kp"):
                stats["agri_scanned"] += 1
                try:
                    html = client.get_text(f"/kp/article/q/{h}.kcmsf", referer=f"{config.BASE_URL}/kp")
                except Exception:
                    continue
                art = parse_article(html, "kp", h)
                if not topics.is_agriculture(f"{art.title or ''} {art.body_text or ''}"):
                    continue
                en_title = en_body = None
                try:
                    en = parse_article(
                        client.get_text(f"/en/article/q/{h}.kcmsf", referer=f"{config.BASE_URL}/en"),
                        "en", h)
                    en_title, en_body = en.title, en.body_text
                    # Many KP articles have no English edition; KCNA then serves a
                    # "The article was not found." page. Don't store that as content.
                    if _looks_not_found(en_title):
                        en_title = None
                    if _looks_not_found(en_body):
                        en_body = None
                except Exception:
                    pass
                if not en_title and translate.available() and art.title:
                    try:
                        en_title = translate.translate_texts([art.title])[0]
                    except Exception:
                        pass
                # If the English edition wasn't available, self-translate the body
                # so the reader still shows English alongside the Korean.
                if not (en_body and en_body.strip()):
                    en_body = _mt_body(art.body_text)
                aid = _store_agri("kcna", h, art.title, en_title, art.body_text, en_body,
                                  f"{config.BASE_URL}/kp/article/q/{h}.kcmsf", art.published_date)
                _extract_agri(aid, art.body_text, en_body or "")
                stats["agri_kept"] += 1
                log.info("agri kcna kept %s — %s", h[:10], (en_title or art.title or "")[:48])
    except Exception as e:
        log.warning("agri kcna pass failed: %s", e)

    # ---- Rodong: Korean only; machine-translate the title ----
    try:
        client = KcnaClient(base_url=config.RODONG_BASE_URL, timeout=config.RODONG_TIMEOUT)
        try:
            client.warmup("ko")
            base = f"{config.RODONG_BASE_URL}/ko/"
            home = client.get_text("/ko/")
            links: dict[str, tuple] = {}
            for href, rid, d in rodong.extract_article_links(home):
                links.setdefault(rid, (href, d))
            for chref, _ko in rodong.extract_category_links(home):
                try:
                    page = client.get_text(f"/ko/{chref}", referer=base)
                except Exception:
                    continue
                for href, rid, d in rodong.extract_article_links(page):
                    links.setdefault(rid, (href, d))
            for rid, (href, d) in links.items():
                stats["agri_scanned"] += 1
                try:
                    html = client.get_text(f"/ko/{href}", referer=base)
                except Exception:
                    continue
                art = rodong.parse_article(html, rid, d)
                if not topics.is_agriculture(f"{art.title or ''} {art.body_text or ''}"):
                    continue
                en_title = None
                if translate.available() and art.title:
                    try:
                        en_title = translate.translate_texts([art.title])[0]
                    except Exception:
                        pass
                en_body = _mt_body(art.body_text)
                aid = _store_agri("rodong", rid, art.title, en_title, art.body_text, en_body,
                                  f"{base}{href}", art.published_date)
                _extract_agri(aid, art.body_text, en_body or "")
                stats["agri_kept"] += 1
                log.info("agri rodong kept %s — %s", rid[:10], (en_title or art.title or "")[:48])
        finally:
            client.close()
    except Exception as e:
        log.warning("agri rodong pass failed: %s", e)

    log.info("agriculture: %s", stats)
    return stats


def backfill_vok_meta() -> int:
    """For already-published VOK videos: make sure a poster frame exists and the
    Korean/English titles are set from config (the title is JS-rendered and can't
    be scraped at publish time). Lets a rebuild fix titles + thumbnails without
    re-downloading or re-transcribing. Returns count touched."""
    with session_scope() as s:
        vids = s.execute(select(Article.hash).where(
            Article.source == "vok", Article.lang == "kp", Article.status == "fetched")).scalars().all()
    n = 0
    for vid in vids:
        folder = config.MEDIA_DIR / f"vok_{vid}"
        mp4, poster = folder / "video.mp4", folder / "poster.jpg"
        if mp4.exists() and mp4.stat().st_size > 0 and not poster.exists():
            _ffmpeg_thumbnail(mp4, poster)
        want = config.VOK_TITLES.get(vid)
        en_want = want
        if want and translate.available() and any('가' <= c <= '힣' for c in want):
            try:
                en_want = translate.translate_texts([want])[0] or want
            except Exception:
                en_want = want
        with session_scope() as s:
            ko = s.scalar(select(Article).where(
                Article.hash == vid, Article.lang == "kp", Article.source == "vok"))
            en = s.scalar(select(Article).where(
                Article.hash == vid, Article.lang == "en", Article.source == "vok"))
            touched = False
            if ko and poster.exists() and not any(m.kind == "image" for m in ko.media):
                s.add(Media(article_id=ko.id, kind="image", source_url=f"vok-poster:{vid}",
                            local_path=f"vok_{vid}/poster.jpg", downloaded=True, position=1))
                touched = True
            if want and ko and ko.title != want:
                ko.title = want
                if en is not None:
                    en.title = en_want
                touched = True
            if touched:
                n += 1
    if n:
        log.info("vok metadata backfilled: %d", n)
    return n


def scrape_vok(limit: int | None = None) -> dict:
    """Subtitle VOK videos: download, transcribe Korean, translate to English,
    and publish a bilingual subtitled player. Heavy and gated behind SCRAPE_VOK."""
    stats = {"vok_fetched": 0}
    ids = [v for v in config.VOK_VIDEO_IDS if not _vok_done(v)]
    if limit:
        ids = ids[:limit]
    if ids:
        log.info("vok queue: %d new video(s) to process", len(ids))
        client = KcnaClient(base_url=config.VOK_BASE_URL, timeout=config.RODONG_TIMEOUT)
        try:
            for vid in ids:
                try:
                    _process_vok_video(client, vid, stats)
                except Exception as e:
                    log.warning("vok %s failed: %s", vid, e)
        finally:
            client.close()
    # Refresh titles/posters for published videos, then build key-words lists.
    stats["vok_meta"] = backfill_vok_meta()
    stats["vok_vocab"] = backfill_vok_vocab(limit)
    log.info("vok: %s", stats)
    return stats


# ---------------------------------------------------------------- magazines
def _magazine_done(mid: str) -> bool:
    with session_scope() as s:
        st = s.scalar(select(Article.status).where(
            Article.hash == mid, Article.lang == "kp", Article.source == "magazine"))
        return st == "fetched"


def _process_magazine(client, title: str, url: str, stats: dict) -> None:
    """Download one issue PDF, render+extract each page, translate, and publish."""
    from . import magazines
    mid = magazines.issue_id_from_url(url)
    folder = config.MEDIA_DIR / f"mag_{mid}"
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / "issue.pdf"
    if not (pdf.exists() and pdf.stat().st_size > 0):
        try:
            data, _ = client.get_bytes(url, referer=f"{config.KOREAN_BOOKS_BASE_URL}/")
        except Exception as e:
            log.warning("magazine %s — download failed: %s", mid, e)
            return
        pdf.write_bytes(data)
    log.info("magazine %s — downloaded (%d bytes), rendering…", mid, pdf.stat().st_size)

    pages = magazines.render_and_extract(pdf, folder)
    if not pages:
        log.warning("magazine %s — no pages rendered (held)", mid)
        return

    # Translate each page's Korean text (blank pages pass through).
    if translate.available():
        try:
            en_list = translate.translate_texts([p["ko"] for p in pages])
            for p, en in zip(pages, en_list):
                p["en"] = en if p["ko"] else ""
        except Exception as e:
            log.warning("magazine %s — translate failed (held, retry next pass): %s", mid, e)
            return
    else:
        log.warning("magazine %s — held: translation unavailable", mid)
        return

    seg_json = json.dumps(pages, ensure_ascii=False)
    rel_poster = f"mag_{mid}/{pages[0]['img']}"
    with session_scope() as s:
        row = s.scalar(select(Article).where(
            Article.hash == mid, Article.lang == "kp", Article.source == "magazine"))
        if row is None:
            row = Article(hash=mid, lang="kp")
            s.add(row)
        row.source = "magazine"
        row.url = url
        row.title = title or f"금수강산 {mid}"
        row.body_text = "\n\n".join(p["ko"] for p in pages if p["ko"])
        row.align_json = seg_json
        row.published_date = None
        row.dedup_key = None
        row.status = "fetched"
        row.scraped_at = datetime.utcnow()
        s.flush()
        if (folder / pages[0]["img"]).exists() and not any(m.kind == "image" for m in row.media):
            s.add(Media(article_id=row.id, kind="image", source_url=f"mag-poster:{mid}",
                        local_path=rel_poster, downloaded=True, position=0))
    ko_pages = sum(1 for p in pages if p["ko"])
    stats["mag_fetched"] += 1
    log.info("magazine %s — published (%d pages, %d with text)", mid, len(pages), ko_pages)


def scrape_magazines(limit: int | None = None) -> dict:
    """Process configured magazine issues (download PDF -> per-page render +
    extract + translate -> publish). Heavy; gated behind SCRAPE_MAGAZINES."""
    stats = {"mag_fetched": 0}
    from . import magazines
    issues = [(t, u) for (t, u) in config.MAGAZINE_ISSUES
              if u and not _magazine_done(magazines.issue_id_from_url(u))]
    if limit:
        issues = issues[:limit]
    if not issues:
        return stats
    log.info("magazine queue: %d issue(s) to process", len(issues))
    client = KcnaClient(base_url=config.KOREAN_BOOKS_BASE_URL, timeout=config.RODONG_TIMEOUT)
    try:
        for title, url in issues:
            try:
                _process_magazine(client, title, url, stats)
            except Exception as e:
                log.warning("magazine %s failed: %s", url, e)
    finally:
        client.close()
    log.info("magazines: %s", stats)
    return stats
