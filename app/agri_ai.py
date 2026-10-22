"""Observation layer for the place-based agriculture analysis (web-importable).

Writes/loads AgriObservation rows (each = one article's claim that a place
produced a tonnage range of a commodity) and runs the aggregation engine over
them. Also handles the copy-paste AI flow (build_prompt + store_ai) and manual
overrides. Kept free of heavy ML deps so the web process imports it cheaply.

Observation kinds:
  rules  — extracted offline from the article text (deterministic).
  ai     — pasted in from a chat assistant's research answer.
  manual — your own override; wins over rules/ai for that place+commodity.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from . import agri_data, agri_extract, agri_model, agri_places as P, llm
from .db import (AgriArticle, AgriCommodity, AgriEndUse, AgriObservation,
                 AgriPlace, session_scope)

log = logging.getLogger("agri_ai")


# --------------------------------------------------------------------------- #
# Places & observations
# --------------------------------------------------------------------------- #
def _upsert_place(s, desc) -> int | None:
    if not desc or not desc.get("norm"):
        return None
    row = s.scalar(select(AgriPlace).where(AgriPlace.norm == desc["norm"]))
    if row is None:
        row = AgriPlace(norm=desc["norm"], name=desc["name"], level=desc["level"],
                        province=desc.get("province"), region=desc.get("region"))
        s.add(row)
        s.flush()
    return row.id


def record_observations(article_id: int, items: list[dict], kind: str) -> int:
    """Replace all `kind` observations for an article with `items`. Each item:
    {commodity, place(descriptor|None), low, high, is_estimate, note}."""
    with session_scope() as s:
        for old in s.execute(select(AgriObservation).where(
                AgriObservation.article_id == article_id,
                AgriObservation.kind == kind)).scalars():
            s.delete(old)
        for it in items:
            pid = _upsert_place(s, it.get("place"))
            s.add(AgriObservation(
                article_id=article_id, place_id=pid, commodity=it["commodity"],
                low_t=it.get("low"), high_t=it.get("high"), kind=kind,
                is_estimate=bool(it.get("is_estimate")), note=it.get("note")))
    return len(items)


def requantify_rules(article_id: int) -> int:
    """Re-run the deterministic rules extraction for one article and replace its
    'rules' observations (place-tagged). Light enough for the web process."""
    with session_scope() as s:
        art = s.get(AgriArticle, article_id)
        if art is None:
            return 0
        ko, en = art.ko_body or "", art.en_body or ""
    place_text = en or ko
    positions = P.place_positions(place_text)
    items = []
    for f in agri_extract.extract_facts(place_text):
        if f.get("tonnes") is None:
            continue
        place = P.nearest_place(f.get("pos", 0), positions) if positions else None
        items.append({"commodity": f["commodity"], "place": place,
                      "low": f["tonnes"], "high": f["tonnes"],
                      "is_estimate": f["is_estimate"], "note": f["phrase"]})
    return record_observations(article_id, items, kind="rules")


def load_observations() -> list[dict]:
    """All observations as plain dicts for agri_model.compute()."""
    with session_scope() as s:
        rows = s.execute(
            select(AgriObservation, AgriPlace)
            .join(AgriPlace, AgriObservation.place_id == AgriPlace.id, isouter=True)).all()
    obs = []
    for o, pl in rows:
        place = None
        if pl is not None:
            place = {"norm": pl.norm, "name": pl.name, "level": pl.level,
                     "province": pl.province, "region": pl.region}
        obs.append({"article_id": o.article_id, "commodity": o.commodity, "place": place,
                    "low": o.low_t, "high": o.high_t, "kind": o.kind})
    return obs


def load_overlays():
    """(commodity_meta, enduse_map) from the DB overlays — AI/manual-discovered
    commodities and end-use splits. Manual end-uses win over AI for a commodity."""
    commodity_meta, by_src = {}, {}
    with session_scope() as s:
        for c in s.execute(select(AgriCommodity)).scalars():
            commodity_meta[c.key] = {"en": c.en, "cat": c.cat, "kind": c.kind,
                                     "kcal_per_kg": c.kcal_per_kg}
        rows = list(s.execute(select(AgriEndUse)).scalars())
    for r in rows:
        by_src.setdefault(r.commodity_key, {}).setdefault(r.source, []).append(
            {"label": r.label, "frac": r.frac, "type": r.use_type, "target": r.target_key})
    enduse_map = {k: (v.get("manual") or v.get("ai")) for k, v in by_src.items()}
    return commodity_meta, enduse_map


def compute_analysis() -> dict:
    meta, emap = load_overlays()
    return agri_model.compute(load_observations(), commodity_meta=meta, enduse_map=emap)


def build_enduse_prompt(commodity_key: str) -> dict | None:
    """Prompt to discover a commodity's full recursive end-use breakdown."""
    meta, _ = load_overlays()
    if commodity_key in meta:
        en = meta[commodity_key]["en"]
    else:
        c = agri_data.COMMODITY_BY_KEY.get(commodity_key)
        if c is None:
            return None
        en = c["en"]
    return {"commodity_key": commodity_key, "commodity_en": en,
            "prompt": llm.build_enduse_prompt(commodity_key, en)}


def _ensure_commodity(s, key, name, kcal, parent=None):
    """Create an overlay commodity for an AI-discovered sub-product (no-op if it's
    already a built-in or already stored)."""
    if key in agri_data.COMMODITY_BY_KEY:
        return
    row = s.scalar(select(AgriCommodity).where(AgriCommodity.key == key))
    if row is None:
        s.add(AgriCommodity(key=key, en=name or key.replace("_", " ").title(),
                            cat="processed", kind="processed",
                            kcal_per_kg=kcal or 0.0, parent_key=parent, source="ai"))
    elif kcal:
        row.kcal_per_kg = kcal


def _store_enduse_tree(key: str, entries: list[dict]) -> None:
    with session_scope() as s:
        for old in s.execute(select(AgriEndUse).where(
                AgriEndUse.commodity_key == key, AgriEndUse.source == "ai")).scalars():
            s.delete(old)
        s.flush()
        for e in entries:
            s.add(AgriEndUse(commodity_key=key, label=e["label"], frac=e["frac"],
                             use_type=e["type"], target_key=e.get("target"), source="ai"))
            prod = e.get("product")
            if prod and prod.get("key"):
                _ensure_commodity(s, prod["key"], prod.get("name"), prod.get("kcal_per_kg"), parent=key)
    # Recurse into sub-products after the parent commit (separate sessions).
    for e in entries:
        prod = e.get("product")
        if prod and prod.get("key") and prod.get("end_uses"):
            _store_enduse_tree(prod["key"], prod["end_uses"])


def store_enduse(commodity_key: str, json_text: str) -> dict | None:
    """Parse a pasted end-use discovery answer and store it (recursively creating
    sub-product commodities and their end-uses). None if unparseable."""
    parsed = llm.parse_enduse(json_text)
    if not parsed:
        return None
    _store_enduse_tree(commodity_key, parsed["end_uses"])
    return parsed


def observations_for_article(article_id: int) -> list[dict]:
    """Quantified observations attached to one article (for its reader/debug)."""
    with session_scope() as s:
        rows = s.execute(
            select(AgriObservation, AgriPlace)
            .join(AgriPlace, AgriObservation.place_id == AgriPlace.id, isouter=True)
            .where(AgriObservation.article_id == article_id)).all()
    out = []
    for o, pl in rows:
        out.append({"commodity": o.commodity, "low": o.low_t, "high": o.high_t,
                    "kind": o.kind, "note": o.note,
                    "place": pl.name if pl is not None else None})
    return out


# --------------------------------------------------------------------------- #
# Copy-paste AI flow
# --------------------------------------------------------------------------- #
def _article(article_id: int):
    with session_scope() as s:
        art = s.get(AgriArticle, article_id)
        if art is None:
            return None
        return (art.en_title or art.ko_title or "", art.en_body or art.ko_body or "")


def build_prompt(article_id: int, commodity_key: str) -> dict | None:
    c = agri_data.COMMODITY_BY_KEY.get(commodity_key)
    info = _article(article_id)
    if c is None or info is None:
        return None
    title, body = info
    return {"commodity_key": commodity_key, "commodity_en": c["en"],
            "title": title, "article_id": article_id,
            "prompt": llm.build_manual_prompt(c["en"], title, body)}


def store_ai(article_id: int, commodity_key: str, json_text: str) -> dict | None:
    """Parse a pasted AI research answer and store it as an 'ai' observation
    (place + low/high range). None if the JSON couldn't be parsed."""
    r = llm.parse_result(json_text)
    if r is None:
        return None
    place = P.describe(r["place"]) if r.get("place") else None
    # Replace only THIS article+commodity's AI estimate, so re-running (e.g. with a
    # newer model) overwrites the old numbers without touching other commodities.
    with session_scope() as s:
        for old in s.execute(select(AgriObservation).where(
                AgriObservation.article_id == article_id,
                AgriObservation.commodity == commodity_key,
                AgriObservation.kind == "ai")).scalars():
            s.delete(old)
        s.flush()
        pid = _upsert_place(s, place)
        s.add(AgriObservation(article_id=article_id, place_id=pid, commodity=commodity_key,
                              low_t=r.get("low"), high_t=r.get("high"), kind="ai",
                              is_estimate=True, note=r.get("basis") or "AI estimate"))
    return r


def set_manual(article_id: int, commodity_key: str, place_name: str | None,
               low: float | None, high: float | None, note: str | None = None) -> bool:
    """Manual override: an authoritative observation that wins over rules/ai for
    this place+commodity. Replaces a prior manual one on the same article."""
    if low is None and high is None:
        return False
    if low is None:
        low = high
    if high is None:
        high = low
    desc = P.describe(place_name) if place_name else None
    with session_scope() as s:
        for old in s.execute(select(AgriObservation).where(
                AgriObservation.article_id == article_id,
                AgriObservation.commodity == commodity_key,
                AgriObservation.kind == "manual")).scalars():
            s.delete(old)
        pid = _upsert_place(s, desc)
        s.add(AgriObservation(article_id=article_id, place_id=pid, commodity=commodity_key,
                              low_t=low, high_t=high, kind="manual", is_estimate=True,
                              note=note or "manual override"))
    return True
