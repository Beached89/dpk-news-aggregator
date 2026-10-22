"""Minimal FastAPI + Jinja2 viewer for the mirrored content.

Phase 1: browse and read mirrored Korean articles with their local media.
The KO/EN toggle scaffold is present and lights up automatically once English
articles (same hash) are scraped in Phase 3.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_

from . import config, agri_ai
from .db import (Article, AgriArticle, AgriMention, AgriObservation, AgriPlace,
                 Category, article_category, init_db, session_scope)

# Subtitle tracks must be served as text/vtt for <track> to load them.
mimetypes.add_type("text/vtt", ".vtt")

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
# Cache-bust the stylesheet whenever it changes (its mtime becomes the ?v= tag),
# so CSS edits show up after a rebuild without a manual hard-refresh.
try:
    templates.env.globals["css_v"] = int((BASE / "static" / "style.css").stat().st_mtime)
except Exception:
    templates.env.globals["css_v"] = 0

app = FastAPI(title="DPRK Learning — KCNA mirror")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    config.ensure_dirs()


# Serve downloaded media straight from the data volume.
app.mount("/media", StaticFiles(directory=str(config.MEDIA_DIR)), name="media")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


def _source_label(source: str) -> str:
    return {"kcna": "KCNA", "rodong": "Rodong"}.get(source, source.title())


def _placeholder_url() -> str | None:
    """URL of the card placeholder logo: prefer Rodong's, fall back to KCNA's."""
    assets = config.MEDIA_DIR / "_assets"
    for cand in sorted(assets.glob("rodong-logo.*")):
        return f"/media/_assets/{cand.name}"
    if (assets / "kcna-logo.png").exists():
        return "/media/_assets/kcna-logo.png"
    return None


def _feed_context(active: str, page: int, per: int, home: bool) -> dict:
    """Shared news-feed builder for the homepage preview (/) and full feed (/news)."""
    with session_scope() as s:
        q = select(Article).where(Article.lang == "kp", Article.status == "fetched",
                                  Article.source.in_(["kcna", "rodong"]))
        if active:
            ac = s.scalar(select(Category).where(Category.key == active))
            if ac:
                q = q.join(article_category).where(article_category.c.category_id == ac.id)
        rows = list(s.execute(
            q.order_by(Article.published_date.desc().nullslast(), Article.scraped_at.desc())
        ).scalars())

        # Group the same story across sources (dedup_key); fallback to hash.
        groups: dict[str, list] = {}
        order: list[str] = []
        for a in rows:
            k = a.dedup_key or ("h:" + a.hash)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(a)
        merged = [sorted(groups[k], key=lambda a: 0 if a.source == "kcna" else 1) for k in order]
        total = len(merged)

        # Lead = most recent group that has a photo (page 1 only).
        def _has_img(grp):
            return any(mm.downloaded and mm.local_path and mm.kind == "image"
                       for m in grp for mm in m.media)
        lead_members = next((g for g in merged if _has_img(g)), None) if page == 1 else None

        # Home shows a 10-item preview (lead + 3×3); /news paginates.
        page_groups = merged[:10] if home else merged[(page - 1) * per: page * per]

        hash_groups = page_groups + ([lead_members] if lead_members is not None else [])
        member_hashes = [m.hash for g in hash_groups for m in g]
        en_titles = {}
        if member_hashes:
            mh_set = set(member_hashes)
            for h, ph, title in s.execute(select(
                    Article.hash, Article.pair_hash, Article.title).where(
                    Article.lang == "en", Article.status == "fetched",
                    or_(Article.hash.in_(member_hashes),
                        Article.pair_hash.in_(member_hashes)))).all():
                en_titles[ph if ph in mh_set else h] = title

        lead = _merge_card(lead_members, en_titles) if lead_members is not None else None
        articles = [_merge_card(g, en_titles) for g in page_groups if g is not lead_members]
        if home:
            articles = articles[:9]                       # lead + 9 = 10

        nav_categories = _catalog(s)
        vid_rows = list(s.execute(
            select(Article).where(
                Article.lang == "kp", Article.status == "fetched", Article.source == "vok")
            .order_by(Article.scraped_at.desc()).limit(10)).scalars())
        side_videos = []
        for a in vid_rows:
            poster = next((f"/media/{m.local_path}" for m in a.media
                           if m.kind == "image" and m.local_path), None)
            side_videos.append({"id": a.hash, "title": a.title or a.hash, "poster": poster})
        mag_rows = list(s.execute(
            select(Article).where(
                Article.lang == "kp", Article.status == "fetched", Article.source == "magazine")
            .order_by(Article.scraped_at.desc()).limit(10)).scalars())
        side_magazines = []
        for a in mag_rows:
            cover = next((f"/media/{m.local_path}" for m in a.media
                          if m.kind == "image" and m.local_path), None)
            side_magazines.append({"id": a.hash, "title": a.title or a.hash, "cover": cover})
    return {
        "articles": articles, "lead": lead, "lang": "kp", "page": page, "total": total, "per": per,
        "has_next": (not home) and page * per < total,
        "nav_categories": nav_categories, "active": active,
        "side_videos": side_videos, "side_magazines": side_magazines,
        "placeholder": _placeholder_url(), "home": home,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Homepage: a preview of the latest 10 news stories + the video rail."""
    return templates.TemplateResponse(request, "index.html", _feed_context("", 1, 10, home=True))


@app.get("/news", response_class=HTMLResponse)
def news(request: Request, page: int = 1, cat: str = "", category: str = ""):
    """The full, paginated news feed (its own route, like /videos)."""
    active = (category or cat or "").strip()
    return templates.TemplateResponse(request, "index.html", _feed_context(active, page, 20, home=False))


@app.get("/article/{hash_}", response_class=HTMLResponse)
def article(request: Request, hash_: str):
    """One merged bilingual article: Korean on top, English beneath, with all
    sources (KCNA + Rodong) linked and their images unioned."""
    with session_scope() as s:
        anchor = s.scalar(select(Article).where(Article.hash == hash_))
        if anchor is None:
            raise HTTPException(404, "Article not mirrored yet")
        ko_anchor = anchor if anchor.lang == "kp" else (
            s.scalar(select(Article).where(Article.hash == hash_, Article.lang == "kp")) or anchor)
        key = ko_anchor.dedup_key if ko_anchor.lang == "kp" else None

        if key:
            ko_members = list(s.execute(select(Article).where(
                Article.lang == "kp", Article.status == "fetched", Article.dedup_key == key)).scalars())
        elif ko_anchor.lang == "kp" and ko_anchor.status == "fetched":
            ko_members = [ko_anchor]
        else:
            ko_members = []
        ko_members.sort(key=lambda a: 0 if a.source == "kcna" else 1)

        # English pairs by shared hash/id with any Korean member (KCNA hash or
        # Rodong rid). Prefer KCNA's English.
        member_hashes = [m.hash for m in ko_members] or [hash_]
        en_rows = list(s.execute(select(Article).where(
            or_(Article.hash.in_(member_hashes), Article.pair_hash.in_(member_hashes)),
            Article.lang == "en", Article.status == "fetched")).scalars())
        en = sorted(en_rows, key=lambda a: 0 if a.source == "kcna" else 1)[0] if en_rows else None
        if not ko_members and en is None:
            raise HTTPException(404, "Article not mirrored yet")

        ko = ko_members[0] if ko_members else None
        # union of all sources' images
        media, seen = [], set()
        for m in ko_members:
            for u in _media_urls(m):
                if u["url"] not in seen:
                    seen.add(u["url"])
                    media.append(u)

        ko_full = _full(ko, media) if ko else None
        en_full = _full(en) if en else None
        ko_text = ko.body_text if ko else None
        en_text = en.body_text if en else None

        sources = [{"label": f"{_source_label(m.source)} · 한국어", "url": m.url} for m in ko_members]
        if en:
            sources.append({"label": f"{_source_label(en.source)} · English", "url": en.url})

        catd = {}
        for m in (ko_members or ([en] if en else [])):
            for c in m.categories:
                if c.key in config.HIDDEN_CATEGORY_KEYS:
                    continue
                catd[c.key] = {"key": c.key, "en": c.label_en, "ko": c.label_ko}
        categories = sorted(catd.values(), key=lambda x: x["en"])
        nav_categories = _catalog(s)

    analysis = None
    align_json = ko.align_json if ko else None
    if align_json:
        import json
        from .analysis import analyze_aligned
        try:
            analysis = analyze_aligned(json.loads(align_json))
        except Exception:
            analysis = None
    if analysis is None and ko_text:
        from .analysis import analyze_article
        analysis = analyze_article(ko_text, en_text)

    # First image shows under the title; any remaining media goes at the bottom.
    display_media = (ko_full or en_full or {}).get("media", [])
    images = [m for m in display_media if m["kind"] == "image"]
    lead_image = images[0] if images else None
    bottom_media = [m for m in display_media if m is not lead_image]

    return templates.TemplateResponse(request, "article.html", {
        "hash": hash_,
        "ko": ko_full, "en": en_full,
        "has_ko": ko_full is not None,
        "has_en": en_full is not None,
        "analysis": analysis,
        "categories": categories,
        "sources": sources,
        "nav_categories": nav_categories,
        "lead_image": lead_image,
        "bottom_media": bottom_media,
    })


def _vtime(t) -> str:
    """Seconds -> M:SS for the transcript timestamp column."""
    t = int(t or 0)
    return f"{t // 60}:{t % 60:02d}"


@app.get("/videos", response_class=HTMLResponse)
def videos(request: Request):
    """List of subtitled VOK videos."""
    with session_scope() as s:
        rows = list(s.execute(
            select(Article).where(
                Article.lang == "kp", Article.status == "fetched", Article.source == "vok")
            .order_by(Article.scraped_at.desc())).scalars())
        cards = []
        for a in rows:
            poster = next((f"/media/{m.local_path}" for m in a.media
                           if m.kind == "image" and m.local_path), None)
            cards.append({"id": a.hash, "title": a.title or a.hash,
                          "snippet": (a.body_text or "")[:140], "poster": poster})
        nav_categories = _catalog(s)
    return templates.TemplateResponse(request, "videos.html", {
        "videos": cards, "nav_categories": nav_categories, "active": "",
        "lang": "kp",
    })


@app.get("/video/{vid}", response_class=HTMLResponse)
def video(request: Request, vid: str):
    """A subtitled VOK video player with a synced bilingual transcript."""
    import json
    with session_scope() as s:
        ko = s.scalar(select(Article).where(
            Article.hash == vid, Article.lang == "kp", Article.source == "vok",
            Article.status == "fetched"))
        if ko is None:
            raise HTTPException(404, "Video not available yet")
        mp4 = next((f"/media/{m.local_path}" for m in ko.media
                    if m.kind == "video" and m.local_path), None)
        if mp4 is None:
            raise HTTPException(404, "Video file missing")
        title = ko.title or vid
        poster = next((f"/media/{m.local_path}" for m in ko.media
                       if m.kind == "image" and m.local_path), None)
        en_row = s.scalar(select(Article).where(
            Article.hash == vid, Article.lang == "en", Article.source == "vok",
            Article.status == "fetched"))
        en_title = en_row.title if en_row else None
        source_url = ko.url
        raw = ko.align_json
        nav_categories = _catalog(s)
    segments = []
    if raw:
        try:
            for seg in json.loads(raw):
                segments.append({
                    "start": seg.get("start", 0), "end": seg.get("end", 0),
                    "ts": _vtime(seg.get("start", 0)),
                    "ko": seg.get("ko", ""), "en": seg.get("en", ""),
                })
        except Exception:
            segments = []
    base = f"/media/vok_{vid}"
    ko_vtt = base + "/ko.vtt" if (config.MEDIA_DIR / f"vok_{vid}" / "ko.vtt").exists() else None
    en_vtt = base + "/en.vtt" if (config.MEDIA_DIR / f"vok_{vid}" / "en.vtt").exists() else None
    # Major Korean words from the transcript, in order of appearance. Prefer the
    # scrape-time vocab.json (dictionary + machine-translated coverage); fall back
    # to the dictionary-only extractor until that's been built.
    vocab = []
    vpath = config.MEDIA_DIR / f"vok_{vid}" / "keywords.json"
    if vpath.exists():
        try:
            vocab = json.loads(vpath.read_text(encoding="utf-8"))
        except Exception:
            vocab = []
    if not vocab and segments:
        from .analysis import extract_vocab
        try:
            vocab = extract_vocab([s["ko"] for s in segments])
        except Exception:
            vocab = []
    return templates.TemplateResponse(request, "video.html", {
        "title": title, "en_title": en_title, "poster": poster,
        "mp4_url": mp4, "ko_vtt": ko_vtt, "en_vtt": en_vtt,
        "segments": segments, "source_url": source_url, "vocab": vocab,
        "nav_categories": nav_categories, "active": "", "lang": "kp",
    })


@app.get("/agriculture", response_class=HTMLResponse)
def agriculture(request: Request):
    """Agriculture/Foodstuffs archive: a reserved analysis area on top, then a
    list of the English titles of every scraped ag/food article."""
    with session_scope() as s:
        rows = list(s.execute(select(AgriArticle).order_by(
            AgriArticle.published_date.desc().nullslast(),
            AgriArticle.scraped_at.desc())).scalars())
        items, seen = [], set()
        for a in rows:
            title = a.en_title or a.ko_title or a.hash
            key = (title or "").strip().lower()
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "id": a.id, "source": a.source, "title": title, "ko_title": a.ko_title,
                "url": a.url,
                "date": a.published_date.isoformat() if a.published_date else None,
            })
        by_source = {}
        for a in rows:
            by_source[a.source] = by_source.get(a.source, 0) + 1
        dates = [a.published_date for a in rows if a.published_date]
        date_range = (f"{min(dates).isoformat()} – {max(dates).isoformat()}") if dates else None

        # --- Place-based aggregation engine (reconciled ranges, hierarchy
        # de-dup, end-use routing, consumable-calorie model) ---
        from . import agri_data
        analysis = agri_ai.compute_analysis()
        cm = analysis["commodities"]
        mention_count = {}
        for mn in s.execute(select(AgriMention)).scalars():
            mention_count.setdefault(mn.commodity, set()).add(mn.article_id)

        primary, processed = [], []
        seen_keys = set()

        def _row(key, data):
            c = agri_data.COMMODITY_BY_KEY.get(key)
            kind = (c["kind"] if c else data.get("kind", "primary"))
            low = data["total_low"] if kind == "processed" else data["base_low"]
            high = data["total_high"] if kind == "processed" else data["base_high"]
            return {
                "key": key, "en": data.get("en") or (c["en"] if c else key.title()),
                "cat": c["cat"] if c else "", "kind": kind,
                "low": low, "high": high, "kcal": data["kcal"], "ai": data["ai"],
                "articles": len(mention_count.get(key, set())),
                "estimable": bool(low) or bool(data["derived_in"]),
                "derived_only": (data["base_low"] == 0 and data["derived_in"] > 0),
            }
        for key, data in cm.items():
            seen_keys.add(key)
            row = _row(key, data)
            (processed if row["kind"] == "processed" else primary).append(row)
        # Commodities only ever *mentioned* (never quantified) still get listed.
        for key, arts in mention_count.items():
            if key in seen_keys:
                continue
            c = agri_data.COMMODITY_BY_KEY.get(key)
            row = {"key": key, "en": c["en"] if c else key.title(),
                   "cat": c["cat"] if c else "", "kind": c["kind"] if c else "primary",
                   "low": 0.0, "high": 0.0, "kcal": 0.0, "ai": False,
                   "articles": len(arts), "estimable": False, "derived_only": False}
            (processed if row["kind"] == "processed" else primary).append(row)

        _sort = lambda L: L.sort(key=lambda r: (r["low"], r["articles"]), reverse=True)
        _sort(primary); _sort(processed)
        pop = analysis["population"]
        nav_categories = _catalog(s)
    return templates.TemplateResponse(request, "agriculture.html", {
        "items": items, "total": len(rows), "unique": len(items),
        "by_source": by_source, "date_range": date_range,
        "resources": primary, "processed": processed,
        "prod_low": analysis["prod_low"], "prod_high": analysis["prod_high"],
        "total_kcal": analysis["total_food_kcal"],
        "per_capita_day": analysis["per_capita_day"],
        "total_has_ai": any(r["ai"] for r in primary + processed),
        "n_quantified": sum(1 for r in primary if r["estimable"]),
        "population": pop, "add_error": request.query_params.get("add_error"),
        "nav_categories": nav_categories, "active": "", "lang": "kp",
    })


@app.get("/agriculture/commodity/{key}", response_class=HTMLResponse)
def agriculture_commodity(request: Request, key: str):
    """Production range + end-use routing + contributing places for one commodity."""
    from . import agri_data
    c = agri_data.COMMODITY_BY_KEY.get(key)
    if c is None:
        raise HTTPException(404, "Unknown commodity")
    analysis = agri_ai.compute_analysis()
    data = analysis["commodities"].get(key)
    with session_scope() as s:
        # Per-place contributions (reconciled would be ideal, but show each place's
        # range and its source articles so overlaps/manual overrides are visible).
        obs_rows = s.execute(
            select(AgriObservation, AgriPlace, AgriArticle)
            .join(AgriPlace, AgriObservation.place_id == AgriPlace.id, isouter=True)
            .join(AgriArticle, AgriObservation.article_id == AgriArticle.id, isouter=True)
            .where(AgriObservation.commodity == key)).all()
        by_place = {}
        for o, pl, ar in obs_rows:
            pkey = pl.norm if pl is not None else f"__art{o.article_id}"
            slot = by_place.setdefault(pkey, {
                "place": pl.name if pl is not None else "(unspecified)",
                "level": pl.level if pl is not None else "", "lows": [], "highs": [],
                "kinds": set(), "articles": [], "manual": False})
            if o.low_t is not None:
                slot["lows"].append(o.low_t)
            if o.high_t is not None:
                slot["highs"].append(o.high_t)
            slot["kinds"].add(o.kind)
            if o.kind == "manual":
                slot["manual"] = True
            if ar is not None:
                slot["articles"].append({"id": ar.id, "title": ar.en_title or ar.ko_title or ar.hash})
        places = []
        for pkey, v in by_place.items():
            lows = [x for x in v["lows"] if x is not None]
            highs = [x for x in v["highs"] if x is not None]
            lo = min(lows) if lows else (min(highs) if highs else 0)
            hi = max(highs) if highs else (max(lows) if lows else 0)
            places.append({"place": v["place"], "level": v["level"], "low": lo, "high": hi,
                           "kinds": sorted(v["kinds"]), "manual": v["manual"],
                           "articles": v["articles"]})
        places.sort(key=lambda x: x["low"], reverse=True)

        # Mentions with no observation yet (candidates to quantify).
        obs_article_ids = {a["id"] for p in places for a in p["articles"]}
        mention_rows = s.execute(
            select(AgriMention, AgriArticle)
            .join(AgriArticle, AgriMention.article_id == AgriArticle.id)
            .where(AgriMention.commodity == key)).all()
        mentions = [{"agri_id": ar.id, "title": ar.en_title or ar.ko_title or ar.hash,
                     "count": mn.count,
                     "date": ar.published_date.isoformat() if ar.published_date else None}
                    for mn, ar in mention_rows if ar.id not in obs_article_ids]
        mentions.sort(key=lambda x: x["count"], reverse=True)
        nav_categories = _catalog(s)

    # End-use breakdown rows (only meaningful for primary commodities with output).
    routes = data["routes"] if data else []
    base_low = data["base_low"] if data else 0.0
    base_high = data["base_high"] if data else 0.0
    total_low = data["total_low"] if data else 0.0
    return templates.TemplateResponse(request, "commodity.html", {
        "c": c, "key": key,
        "base_low": base_low, "base_high": base_high,
        "total_low": total_low,
        "derived_in": data["derived_in"] if data else 0.0,
        "food_tonnes": data["food_tonnes"] if data else 0.0,
        "kcal": data["kcal"] if data else 0.0,
        "routes": routes, "places": places, "mentions": mentions,
        "total_has_ai": data["ai"] if data else False,
        "nav_categories": nav_categories, "active": "", "lang": "kp",
    })


@app.post("/agriculture/requantify")
def agriculture_requantify(commodity: str = Form(...), article_id: int = Form(...)):
    """Re-run the deterministic rules extraction for one article (re-parse)."""
    try:
        agri_ai.requantify_rules(article_id)
    except Exception:
        pass
    return RedirectResponse(url=f"/agriculture/commodity/{commodity}", status_code=303)


@app.post("/agriculture/override")
def agriculture_override(commodity: str = Form(...), place: str = Form(""),
                         low: str = Form(""), high: str = Form(""),
                         article_id: str = Form("")):
    """Manual override: record an authoritative tonnage (range) for a place."""
    def _f(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None
    aid = int(article_id) if str(article_id).strip().isdigit() else None
    try:
        agri_ai.set_manual(aid, commodity, place.strip() or None, _f(low), _f(high))
    except Exception:
        pass
    return RedirectResponse(url=f"/agriculture/commodity/{commodity}", status_code=303)


@app.get("/agriculture/estimate/manual", response_class=HTMLResponse)
def agriculture_estimate_manual(request: Request, commodity: str, article_id: int):
    """Copy-paste flow: show a ready-made prompt the user feeds to any chat
    assistant (Claude.ai, Gemini, …), plus a box to paste the JSON answer back."""
    info = agri_ai.build_prompt(article_id, commodity)
    if info is None:
        raise HTTPException(404, "Unknown commodity or article")
    with session_scope() as s:
        nav_categories = _catalog(s)
    ctx = dict(info)
    ctx.update({"nav_categories": nav_categories, "active": "", "lang": "kp",
                "saved": request.query_params.get("saved"),
                "error": request.query_params.get("error")})
    return templates.TemplateResponse(request, "estimate_manual.html", ctx)


@app.post("/agriculture/estimate/manual")
def agriculture_estimate_manual_save(commodity: str = Form(...), article_id: int = Form(...),
                                     result_json: str = Form("")):
    """Store the JSON the user pasted from their assistant. On parse failure,
    bounce back to the prompt page with an error flag."""
    ok = False
    try:
        ok = bool(agri_ai.store_ai(article_id, commodity, result_json))
    except Exception:
        ok = False
    if ok:
        return RedirectResponse(url=f"/agriculture/commodity/{commodity}", status_code=303)
    return RedirectResponse(
        url=f"/agriculture/estimate/manual?commodity={commodity}&article_id={article_id}&error=1",
        status_code=303)


@app.get("/agriculture/enduse", response_class=HTMLResponse)
def agriculture_enduse_prompt(request: Request, commodity: str):
    """Copy-paste flow to discover a commodity's full recursive end-use breakdown."""
    info = agri_ai.build_enduse_prompt(commodity)
    if info is None:
        raise HTTPException(404, "Unknown commodity")
    with session_scope() as s:
        nav_categories = _catalog(s)
    ctx = dict(info)
    ctx.update({"nav_categories": nav_categories, "active": "", "lang": "kp",
                "error": request.query_params.get("error")})
    return templates.TemplateResponse(request, "enduse_discover.html", ctx)


@app.post("/agriculture/enduse")
def agriculture_enduse_save(commodity: str = Form(...), result_json: str = Form("")):
    """Store a pasted end-use discovery answer (recursively)."""
    ok = False
    try:
        ok = bool(agri_ai.store_enduse(commodity, result_json))
    except Exception:
        ok = False
    if ok:
        return RedirectResponse(url=f"/agriculture/commodity/{commodity}", status_code=303)
    return RedirectResponse(url=f"/agriculture/enduse?commodity={commodity}&error=1", status_code=303)


@app.post("/agriculture/add-url")
def agriculture_add_url(url: str = Form("")):
    """Ingest a single pasted article URL: fetch -> parse -> translate -> store ->
    quantify, then open it. Loads the scraper lazily so normal web startup stays
    light."""
    from urllib.parse import quote
    try:
        from . import scraper
        res = scraper.ingest_url(url)
        return RedirectResponse(url=f"/agriculture/{res['id']}", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/agriculture?add_error={quote(str(e)[:200])}",
                                status_code=303)


@app.get("/agriculture/{aid}", response_class=HTMLResponse)
def agriculture_article(request: Request, aid: int):
    """On-site bilingual reader for a locally-stored agriculture article."""
    with session_scope() as s:
        a = s.get(AgriArticle, aid)
        if a is None:
            raise HTTPException(404, "Article not found")
        ko_paras = [p for p in (a.ko_body or "").split("\n\n") if p.strip()]
        en_paras = [p for p in (a.en_body or "").split("\n\n") if p.strip()]
        ctx = {
            "ko_title": a.ko_title, "en_title": a.en_title,
            "ko_paras": ko_paras, "en_paras": en_paras,
            "source": a.source, "url": a.url,
            "date": a.published_date.isoformat() if a.published_date else None,
            "nav_categories": _catalog(s), "active": "", "lang": "kp",
        }
    return templates.TemplateResponse(request, "agri_article.html", ctx)


@app.get("/magazines", response_class=HTMLResponse)
def magazines(request: Request):
    """List of processed magazine issues."""
    with session_scope() as s:
        rows = list(s.execute(
            select(Article).where(
                Article.lang == "kp", Article.status == "fetched", Article.source == "magazine")
            .order_by(Article.scraped_at.desc())).scalars())
        cards = []
        for a in rows:
            cover = next((f"/media/{m.local_path}" for m in a.media
                          if m.kind == "image" and m.local_path), None)
            cards.append({"id": a.hash, "title": a.title or a.hash, "cover": cover})
        nav_categories = _catalog(s)
    return templates.TemplateResponse(request, "magazines.html", {
        "magazines": cards, "nav_categories": nav_categories, "active": "", "lang": "kp",
    })


@app.get("/magazine/{mid}", response_class=HTMLResponse)
def magazine(request: Request, mid: str):
    """A magazine reader: one page image on the left, Korean + English on the
    right, with page-flip navigation."""
    import json
    with session_scope() as s:
        row = s.scalar(select(Article).where(
            Article.hash == mid, Article.lang == "kp", Article.source == "magazine",
            Article.status == "fetched"))
        if row is None:
            raise HTTPException(404, "Magazine not available yet")
        title = row.title or mid
        source_url = row.url
        raw = row.align_json
        nav_categories = _catalog(s)
    pages = []
    if raw:
        try:
            for p in json.loads(raw):
                pages.append({"img": f"/media/mag_{mid}/{p.get('img')}",
                              "ko": p.get("ko", ""), "en": p.get("en", "")})
        except Exception:
            pages = []
    return templates.TemplateResponse(request, "magazine.html", {
        "title": title, "pages": pages, "source_url": source_url,
        "nav_categories": nav_categories, "active": "", "lang": "kp",
    })


# ---- 조선어 · Hangul/Chosŏn'gŭl: official DPRK language texts -----------------
# Scanned DPRK-published Korean textbooks (Pyongyang standard) from the Internet
# Archive item "letslearnkorean" (Foreign Languages Publishing House, 1995).
# Self-hosted when the PDF is present in MEDIA_DIR/_library/...; otherwise the
# page streams/links the original from the Internet Archive.
HANGUL_COLLECTION = {
    "title": "Learn Korean — Pyongyang Dialect",
    "publisher": "Foreign Languages Publishing House, Pyongyang (1995)",
    "archive_id": "letslearnkorean",
    "subdir": "pyongyang-dialect",
    "texts": [
        {"title": "Let's Learn Korean", "sub": "Volume 1", "file": "Lets-Learn-Korean-1.pdf",
         "archive": "Let%27s%20Learn%20Korean%201-of-4_rotated_text.pdf"},
        {"title": "Let's Learn Korean", "sub": "Volume 2", "file": "Lets-Learn-Korean-2.pdf",
         "archive": "Let%27s%20Learn%20Korean%202-of-4_rotated_text.pdf"},
        {"title": "Let's Learn Korean", "sub": "Volume 3", "file": "Lets-Learn-Korean-3.pdf",
         "archive": "Let%27s%20Learn%20Korean%203-of-4_rotated_text.pdf"},
        {"title": "Let's Learn Korean", "sub": "Volume 4", "file": "Lets-Learn-Korean-4.pdf",
         "archive": "Let%27s%20Learn%20Korean%204-of-4_rotated_text.pdf"},
        {"title": "Learn Korean on Your Own", "sub": None, "file": "Learn-Korean-on-Your-Own.pdf",
         "archive": "Learn%20Korean%20On%20Your%20Own_text.pdf"},
        {"title": "조선어 (Chosŏn-ŏ) 1", "sub": "Korean primer", "file": "Choson-O-1.pdf",
         "archive": "Choson_O_1_text.pdf"},
    ],
}


@app.get("/hangul", response_class=HTMLResponse)
def hangul(request: Request):
    """The 조선어 · Hangul/Chosŏn'gŭl reading room of official DPRK language texts."""
    col = HANGUL_COLLECTION
    base_dir = config.MEDIA_DIR / "_library" / col["subdir"]
    arch = f"https://archive.org/download/{col['archive_id']}"
    texts = []
    local_count = 0
    for t in col["texts"]:
        local = base_dir / t["file"]
        has_local = local.exists() and local.stat().st_size > 0
        if has_local:
            local_count += 1
        texts.append({
            "title": t["title"],
            "sub": t.get("sub"),
            "local_url": f"/media/_library/{col['subdir']}/{t['file']}" if has_local else None,
            "archive_url": f"{arch}/{t['archive']}",
        })
    with session_scope() as s:
        nav_categories = _catalog(s)
    return templates.TemplateResponse(request, "hangul.html", {
        "collection": col, "texts": texts, "local_count": local_count,
        "all_local": local_count == len(texts),
        "archive_item": f"https://archive.org/details/{col['archive_id']}",
        "embed_url": f"https://archive.org/embed/{col['archive_id']}",
        "drop_path": "data/media/_library/" + col["subdir"] + "/",
        "nav_categories": nav_categories, "active": "", "lang": "kp",
    })


def _catalog(s) -> list[dict]:
    """The full category list for the header nav (ordered), minus hidden ones."""
    return [{"key": c.key, "label_ko": c.label_ko, "label_en": c.label_en}
            for c in s.execute(select(Category).order_by(Category.sort, Category.label_en)).scalars()
            if c.key not in config.HIDDEN_CATEGORY_KEYS]


def _media_urls(a: Article) -> list[dict]:
    out = []
    for m in sorted(a.media, key=lambda x: x.position):
        if m.downloaded and m.local_path:
            out.append({"kind": m.kind, "url": f"/media/{m.local_path}"})
    return out


def _card(a: Article, en_title: str | None = None) -> dict:
    imgs = _media_urls(a)
    return {
        "hash": a.hash,
        "title": a.title or "(제목 없음)",
        "en_title": en_title,
        "subtitle": a.subtitle,
        "date": a.published_date.isoformat() if a.published_date else None,
        "thumb": next((m["url"] for m in imgs if m["kind"] == "image"), None),
        "snippet": (a.body_text or "")[:140],
        "has_translation": en_title is not None,
        "categories": sorted(({"key": c.key, "en": c.label_en, "ko": c.label_ko}
                              for c in a.categories if c.key not in config.HIDDEN_CATEGORY_KEYS),
                             key=lambda x: x["en"]),
    }


def _full(a: Article, media: list | None = None) -> dict:
    return {
        "hash": a.hash, "lang": a.lang,
        "title": a.title, "subtitle": a.subtitle,
        "date": a.published_date.isoformat() if a.published_date else None,
        "paragraphs": [p for p in (a.body_text or "").split("\n\n") if p.strip()],
        "media": media if media is not None else _media_urls(a),
        "source_url": a.url,
    }


def _merge_card(members: list, en_titles: dict) -> dict:
    """Build a homepage card from a dedup group (members across sources)."""
    rep = members[0]
    thumb, catd = None, {}
    for m in members:
        if thumb is None:
            thumb = next((u["url"] for u in _media_urls(m) if u["kind"] == "image"), None)
        for c in m.categories:
            if c.key in config.HIDDEN_CATEGORY_KEYS:
                continue
            catd[c.key] = {"key": c.key, "en": c.label_en, "ko": c.label_ko}
    en_title = next((en_titles.get(m.hash) for m in members if en_titles.get(m.hash)), None)
    return {
        "hash": rep.hash,
        "title": rep.title or "(제목 없음)",
        "en_title": en_title,
        "date": rep.published_date.isoformat() if rep.published_date else None,
        "thumb": thumb,
        "snippet": (rep.body_text or "")[:140],
        "has_translation": en_title is not None,
        "categories": sorted(catd.values(), key=lambda x: x["en"]),
        "sources": sorted({m.source for m in members}),
    }


def serve() -> None:
    import uvicorn
    uvicorn.run("app.web:app", host=config.WEB_HOST, port=config.WEB_PORT)


if __name__ == "__main__":
    serve()
