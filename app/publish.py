"""Export the SQLite mirror to static JSON for Cloudflare Pages.

Why this module exists: Cloudflare Pages serves static files, so the FastAPI app
cannot run there. Everything the front end needs is therefore written out as
plain JSON next to the HTML on each CI run, and the browser does the rendering.

Deliberate constraint: **stdlib only** (sqlite3, json, pathlib). The export step
must run on a bare interpreter — no SQLAlchemy, no selectolax, no torch — so a
nightly deploy never fails on the heavy scraping dependencies, and so the export
can be reasoned about (and tested) without the whole app importable. It opens the
database read-only and never writes to it.

Output layout (`--out`, default `site/`):

    index.html              copied from the repo root (--static-assets)
    assets/...              ditto
    data/manifest.json      generated_at, schema version, counts, flags
    data/categories.json    category nav with per-category counts
    data/articles.json      the story list, newest first (snippets + covers)
    data/a/<hash>.json      one file per story: full KO + EN + word alignment
    data/agri.json          agriculture facts rollup (only if those tables exist)

Story identity mirrors the app: a `hash` is one article, its Korean text lives on
the `lang='kp'` row and the English (own-scraped or machine-generated) on the
`lang='en'` row of the same hash; `dedup_key` collapses the same story scraped
from KCNA and Rodong into one card.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("publish")

SCHEMA_VERSION = 1
_HIDDEN = "kp"        # stored language code for Korean


# ------------------------------------------------------------------ loading
def _connect(db_path: Path) -> sqlite3.Connection:
    """Open the mirror read-only, falling back to a normal handle.

    A WAL-mode database with a hot journal cannot always be opened read-only
    (SQLite may need the -shm/-wal files), which would be a confusing failure in
    CI, so try `mode=ro` first and degrade rather than error out. We never write.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.execute("SELECT count(*) FROM articles").fetchone()
    except sqlite3.Error:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _media_of(conn: sqlite3.Connection, ids: list[int],
              media_base: str) -> dict[int, list[dict]]:
    """Downloaded media grouped by article id, in display order."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out: dict[int, list[dict]] = {}
    for m in conn.execute(
            f"SELECT article_id, kind, source_url, local_path, position "
            f"FROM media WHERE downloaded = 1 AND article_id IN ({marks}) "
            f"ORDER BY position, id", ids).fetchall():
        local = m["local_path"]
        remote = m["source_url"] or ""
        url = remote if remote.startswith(("http://", "https://")) else \
            (f"{media_base}/{local}" if local else "")
        out.setdefault(m["article_id"], []).append(
            {"kind": m["kind"], "url": url, "local": local, "position": m["position"]})
    return out


def _categories_of(conn: sqlite3.Connection,
                   hidden: set[str]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in conn.execute(
            "SELECT ac.article_id AS aid, c.key, c.label_en, c.label_ko "
            "FROM article_category ac JOIN categories c ON c.id = ac.category_id "
            "ORDER BY c.sort, c.label_en").fetchall():
        if r["key"] in hidden:
            continue
        out.setdefault(r["aid"], []).append(
            {"key": r["key"], "en": r["label_en"], "ko": r["label_ko"]})
    return out


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


# ------------------------------------------------------------------ building
def _load_rows(conn: sqlite3.Connection, max_age_hours: int) -> list[sqlite3.Row]:
    """Every successfully fetched article row (both languages) newest-first.

    Rows with no published_date are kept and sorted last: those are the curated
    feeds (VOK videos, magazine issues) which the scraper deliberately exempts
    from the rolling age window.
    """
    where = ["a.status = 'fetched'"]
    args: list[Any] = []
    if max_age_hours > 0:
        where.append("(a.published_date IS NULL OR a.published_date >= ?)")
        args.append((datetime.now(timezone.utc)
                     - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d"))
    rows = conn.execute(
        f"SELECT a.id, a.hash, a.lang, a.source, a.url, a.title, a.subtitle, "
        f"       a.body_text, a.published_date, a.dateline, a.dedup_key, a.align_json "
        f"FROM articles a WHERE {' AND '.join(where)} "
        f"ORDER BY (a.published_date IS NULL), a.published_date DESC, a.id DESC",
        args).fetchall()
    return rows


def _stories(conn: sqlite3.Connection, rows: list[sqlite3.Row],
             hidden: set[str], media_base: str) -> list[dict]:
    """Fold (hash, lang) rows into stories, then collapse cross-source twins.

    One `hash` = one article: Korean text on the kp row, English (own-scraped or
    machine-generated) on the en row. Stories that share a `dedup_key` are the
    same report in KCNA and Rodong, so they become one card listing both sources.
    """
    cats = _categories_of(conn, hidden)
    media = _media_of(conn, [r["id"] for r in rows], media_base)

    by_hash: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        h = r["hash"]
        st = by_hash.get(h)
        if st is None:
            st = {
                "id": h,
                "date": _iso(r["published_date"]),
                "sources": [],
                "url": r["url"],
                "title_ko": None, "title_en": None,
                "body_ko": None, "body_en": None,
                "dateline": r["dateline"], "subtitle": r["subtitle"],
                "dedup_key": r["dedup_key"], "align": None,
                "categories": [], "media": [], "_ids": [],
            }
            by_hash[h] = st
            order.append(h)
        st["_ids"].append(r["id"])
        if r["source"] and r["source"] not in st["sources"]:
            st["sources"].append(r["source"])
        if r["lang"] == _HIDDEN:                      # Korean side
            st["title_ko"] = st["title_ko"] or r["title"]
            st["body_ko"] = st["body_ko"] or r["body_text"]
            if r["align_json"]:
                st["align"] = r["align_json"]
        else:                                         # English side
            st["title_en"] = st["title_en"] or r["title"]
            st["body_en"] = st["body_en"] or r["body_text"]
        if not st["url"] and r["url"]:
            st["url"] = r["url"]
        if not st["dateline"] and r["dateline"]:
            st["dateline"] = r["dateline"]

    stories = []
    for h in order:
        st = by_hash[h]
        seen_cat: dict[str, dict] = {}
        for aid in st["_ids"]:
            for c in cats.get(aid, []):
                seen_cat[c["key"]] = c
            for m in media.get(aid, []):
                if m["url"] and m["url"] not in {x["url"] for x in st["media"]}:
                    st["media"].append(m)
        st["categories"] = list(seen_cat.values())
        stories.append(st)

    # Collapse same-story rows from different sources into one card.
    group_of: dict[str, list[str]] = {}
    for st in stories:
        if st["dedup_key"]:
            group_of.setdefault(st["dedup_key"], []).append(st["id"])
    for st in stories:
        peers = group_of.get(st["dedup_key"]) or [st["id"]]
        st["group"] = peers
        for p in peers:
            if p != st["id"]:
                for src in by_hash[p]["sources"]:
                    if src not in st["sources"]:
                        st["sources"].append(src)
    return stories


def _card(st: dict) -> dict:
    """The compact list shape: enough for a card, nothing that bloats the feed."""
    body = st["body_ko"] or st["body_en"] or ""
    cover = next((m["url"] for m in st["media"] if m["kind"] == "image"), None)
    return {
        "id": st["id"],
        "date": st["date"],
        "sources": st["sources"],
        "title_ko": st["title_ko"] or st["title_en"] or st["id"],
        "title_en": st["title_en"] or "",
        "dateline": st["dateline"],
        "categories": st["categories"],
        "cover": cover,
        "videos": [m["url"] for m in st["media"] if m["kind"] == "video"],
        "snippet": body[:180].strip(),
        "has_translation": bool(st["title_en"] or st["body_en"]),
        "has_alignment": bool(st["align"]),
        "group": st["group"],
    }


def _detail(st: dict) -> dict:
    out = dict(st)
    out.pop("_ids", None)
    align = out.get("align")
    if isinstance(align, str):
        try:
            out["align"] = json.loads(align)
        except (ValueError, TypeError):
            log.warning("story %s has unparsable align_json; dropping alignment",
                        st["id"])
            out["align"] = None
    return out


def _agri(conn: sqlite3.Connection, tables: set[str]) -> dict | None:
    """Agriculture dashboard payload: facts, place observations, lookups.

    Every table is checked before it is queried: the agri schema was added in
    stages, and a mirror that has none of those tables yet must still publish.
    """
    if "agri_articles" not in tables:
        return None
    out: dict[str, Any] = {}
    if "agri_facts" in tables:
        facts = [dict(r) for r in conn.execute(
            "SELECT a.hash, a.source, a.published_date, f.commodity, f.metric, "
            "       f.value, f.unit, f.tonnes, f.kcal, f.is_estimate, f.is_ai "
            "FROM agri_facts f JOIN agri_articles a ON a.id = f.article_id "
            "ORDER BY a.id DESC LIMIT 5000").fetchall()]
        for f in facts:
            f["is_estimate"] = bool(f["is_estimate"])
            f["is_ai"] = bool(f.get("is_ai"))
        out["facts"] = facts
    if "agri_observations" in tables:
        obs = [dict(r) for r in conn.execute(
            "SELECT o.commodity, o.low_t, o.high_t, o.kind, o.is_estimate, "
            "       p.name AS place, p.level, p.province, a.hash "
            "FROM agri_observations o "
            "LEFT JOIN agri_places p ON p.id = o.place_id "
            "LEFT JOIN agri_articles a ON a.id = o.article_id "
            "ORDER BY o.id DESC LIMIT 5000").fetchall()]
        for o in obs:
            o["is_estimate"] = bool(o["is_estimate"])
        out["observations"] = obs
    if "agri_commodities" in tables:
        out["commodities"] = [dict(r) for r in conn.execute(
            "SELECT key, en, cat, kind, kcal_per_kg, parent_key "
            "FROM agri_commodities ORDER BY key").fetchall()]
    if "agri_enduses" in tables:
        out["enduses"] = [dict(r) for r in conn.execute(
            "SELECT commodity_key, label, frac, use_type FROM agri_enduses "
            "ORDER BY commodity_key, frac DESC").fetchall()]
    return out or None


# ------------------------------------------------------------------- writing
def _write(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def _copy_static(out: Path, root: Path) -> list[str]:
    """Copy the front end into the deploy dir — and refuse to publish anything else.

    The Pages build output used to be the repository root, which published app/
    source code (including how the scrapers and API keys are read) to the public
    internet. `site/` is the only deployable directory from now on, and this is
    the only way non-generated files get into it.
    """
    copied: list[str] = []
    index = root / "index.html"
    if index.exists():
        text = index.read_text(encoding="utf-8", errors="replace")
        if "{%" in text or "{{ " in text:
            log.warning("index.html looks like a Jinja template — static hosting "
                        "will serve the placeholders literally")
        shutil.copy2(index, out / "index.html")
        copied.append("index.html")
    else:
        log.warning("no index.html at %s; deploying data only", root)
    assets = root / "assets"
    if assets.is_dir():
        dest = out / "assets"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(assets, dest)
        copied.append("assets/")
    return copied


def publish(db: Path, out: Path, *, max_age_hours: int = 0, limit: int = 0,
            hidden: set[str] | None = None, media_base: str = "",
            static_assets: bool = True, clean: bool = True,
            root: Path | None = None) -> dict:
    """Write the static site for `db` into `out`; return the manifest."""
    hidden = config.HIDDEN_CATEGORY_KEYS if hidden is None else hidden
    root = root or Path(__file__).resolve().parent.parent
    out.mkdir(parents=True, exist_ok=True)
    data = out / "data"
    if clean and data.exists():
        shutil.rmtree(data)                 # stale articles must not linger

    conn = _connect(Path(db))
    try:
        tables = _tables(conn)
        if "articles" not in tables:
            raise SystemExit(f"no articles table in {db} — nothing to publish")
        rows = _load_rows(conn, max_age_hours)
        stories = _stories(conn, rows, hidden, media_base)
        agri = _agri(conn, tables)
        labels = {r["key"]: {"en": r["label_en"], "ko": r["label_ko"]}
                  for r in conn.execute(
                      "SELECT key, label_en, label_ko FROM categories").fetchall()
                  if r["key"] not in hidden}
    finally:
        conn.close()

    truncated = limit > 0 and len(stories) > limit
    if truncated:
        stories = stories[:limit]

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cards = [_card(s) for s in stories]

    size = _write(data / "articles.json",
                  {"generated_at": generated, "schema": SCHEMA_VERSION,
                   "count": len(cards), "truncated": truncated,
                   "stories": cards})
    for s in stories:
        size += _write(data / "a" / f"{s['id']}.json", _detail(s))

    counts: dict[str, int] = {}
    for c in cards:
        for cat in c["categories"]:
            counts[cat["key"]] = counts.get(cat["key"], 0) + 1
    nav = [{"key": k, **labels.get(k, {"en": k, "ko": k}), "count": n}
           for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    size += _write(data / "categories.json", {"generated_at": generated,
                                              "categories": nav})
    if agri is not None:
        agri["generated_at"] = generated
        size += _write(data / "agri.json", agri)

    manifest = {
        "generated_at": generated,
        "schema": SCHEMA_VERSION,
        "stories": len(cards),
        "translated": sum(1 for c in cards if c["has_translation"]),
        "aligned": sum(1 for c in cards if c["has_alignment"]),
        "with_photo": sum(1 for c in cards if c["cover"]),
        "sources": sorted({s for c in cards for s in c["sources"]}),
        "window_hours": max_age_hours or None,
        "truncated": truncated,
        "media_local": bool(media_base),
        "bytes": size,
    }
    _write(data / "manifest.json", manifest)

    if static_assets:
        manifest["static"] = _copy_static(out, root)
    log.info("published %d stories (%d translated, %d bytes) to %s",
             manifest["stories"], manifest["translated"], size, out)
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(
        description="Export the SQLite mirror to static JSON for Cloudflare Pages")
    p.add_argument("--db", default=str(config.DB_PATH), help="path to kcna.db")
    p.add_argument("--out", default="site", help="deploy directory (Pages output)")
    p.add_argument("--max-age-hours", type=int, default=0,
                   help="skip stories older than this (0 = export everything)")
    p.add_argument("--limit", type=int, default=0,
                   help="publish at most this many stories (0 = all)")
    p.add_argument("--media-base", default="/media",
                   help="URL prefix for mirrored media (web.py serves /media; "
                        "use an R2 public URL once media lives there)")
    p.add_argument("--no-static", action="store_true",
                   help="write data/ only; leave index.html and assets alone")
    p.add_argument("--no-clean", action="store_true",
                   help="keep existing data/ instead of rebuilding it")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    manifest = publish(Path(args.db), Path(args.out),
                       max_age_hours=args.max_age_hours, limit=args.limit,
                       media_base=args.media_base,
                       static_assets=not args.no_static,
                       clean=not args.no_clean)
    json.dump(manifest, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()

