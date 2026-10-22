"""Offline, rule-based quantity extractor for agriculture articles.

Pulls (commodity, metric, value, unit) facts from article text — explicit
numbers ("50 hectares of apples", "1,000 tons"), Korean digit multipliers
("5천정보", "1만톤"), and fuzzy quantifiers ("수만 톤", "scores of hectares").
Area facts are converted to an estimated tonnage via the commodity's average
yield × YIELD_FACTOR (0.75). Deterministic, no network/LLM — safe to run on
every newly scraped article so the totals stay live.
"""
from __future__ import annotations

import re

from . import agri_data as D

_AREA = {"정보": 1.0, "헥타르": 1.0, "ha": 1.0, "hectare": 1.0, "hectares": 1.0,
         "acre": 0.404686, "acres": 0.404686, "에이커": 0.404686}
_MASS = {"톤": 1.0, "tonne": 1.0, "tonnes": 1.0, "ton": 1.0, "tons": 1.0,
         "kg": 0.001, "키로그람": 0.001, "킬로그람": 0.001}
_COUNT = {"마리": 1.0, "head": 1.0}

_KO_FUZZY = {"수십만": 300_000, "수만": 30_000, "수천": 3_000, "수백": 300, "수십": 50}
_EN_FUZZY = {"tens of thousands": 30_000, "hundreds of thousands": 300_000,
             "thousands": 3_000, "hundreds": 300, "scores": 60, "dozens": 36, "several": 5}

_UNIT_ALT = ("정보|헥타르|에이커|톤|키로그람|킬로그람|마리|hectares|hectare|"
             "acres|acre|tonnes|tonne|tons|ton|kg|ha|head")
_RE_EXPLICIT = re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*([십백천만억])?\s*(' + _UNIT_ALT + r')', re.I)
_RE_KO_FUZZY = re.compile(r'(수십만|수만|수천|수백|수십)\s*(' + _UNIT_ALT + r')')
_RE_EN_FUZZY = re.compile(
    r'\b(tens of thousands|hundreds of thousands|thousands|hundreds|scores|dozens|several)\s+'
    r'(?:of\s+)?(' + _UNIT_ALT + r')\b', re.I)


def _unit_kind(u: str):
    u = u.lower()
    if u in _AREA:
        return "area", _AREA[u]
    if u in _MASS:
        return "mass", _MASS[u]
    if u in _COUNT:
        return "count", _COUNT[u]
    return None, None


# Korean commodity terms that ALSO mean something common and unrelated, so they
# must never trigger a commodity by themselves. The worst offender is 가지, which
# means "eggplant" but far more often "kind/variety" (여러 가지 = "various kinds").
# These are skipped in Korean matching; the unambiguous English term still works.
_AMBIGUOUS_KO = {
    "가지",   # eggplant / "kinds, varieties"
    "대두",   # soybean / "emergence, looming"
    "수수",   # sorghum / "plain, modest" / "fee"
    "정조",   # unhulled rice (正租) / "chastity, fidelity"
}


def _commodity_positions(text: str):
    """List of (pos, commodity_key) for every commodity term found."""
    low = text.lower()
    hits = []
    for c in D.COMMODITIES:
        for t in c["ko"]:
            if t in _AMBIGUOUS_KO:
                continue
            i = text.find(t)
            if i >= 0:
                hits.append((i, c["key"]))
        for t in c["en_terms"]:
            i = low.find(t)
            if i >= 0:
                hits.append((i, c["key"]))
    return hits


def _nearest_commodity(pos: int, commodities, window: int = 120):
    best, bestd = None, window + 1
    for cpos, key in commodities:
        d = abs(cpos - pos)
        if d < bestd:
            best, bestd = key, d
    return best


def extract_facts(text: str) -> list[dict]:
    """Return a list of fact dicts extracted from `text`. Each fact:
       {commodity, metric, value, unit, tonnes, kcal, is_estimate, phrase}."""
    if not text:
        return []
    commodities = _commodity_positions(text)
    if not commodities:
        return []

    raw_facts = []  # (pos, value, unit, phrase)
    for m in _RE_EXPLICIT.finditer(text):
        try:                       # strip ASCII + fullwidth thousands separators
            num = float(m.group(1).replace(",", "").replace("，", ""))
        except ValueError:
            continue
        mult = D.KO_MULTIPLIERS.get(m.group(2) or "", 1)
        raw_facts.append((m.start(), num * mult, m.group(3), m.group(0)))
    for m in _RE_KO_FUZZY.finditer(text):
        raw_facts.append((m.start(), _KO_FUZZY[m.group(1)], m.group(2), m.group(0)))
    for m in _RE_EN_FUZZY.finditer(text):
        raw_facts.append((m.start(), _EN_FUZZY[m.group(1).lower()], m.group(2), m.group(0)))

    facts, seen = [], set()
    for pos, value, unit, phrase in raw_facts:
        kind, factor = _unit_kind(unit)
        if kind is None:
            continue
        key = _nearest_commodity(pos, commodities)
        if not key:
            continue
        c = D.COMMODITY_BY_KEY[key]
        tonnes = None
        is_estimate = False
        if kind == "mass":
            tonnes = value * factor
        elif kind == "area":
            ha = value * factor
            if c["yield_t_ha"]:
                tonnes = ha * c["yield_t_ha"] * D.YIELD_FACTOR
                is_estimate = True
        # count -> no tonnage
        kcal = (tonnes * 1000 * c["kcal_per_kg"]) if tonnes else None
        dedup = (key, kind, round(value, 3), unit.lower())
        if dedup in seen:
            continue
        seen.add(dedup)
        facts.append({
            "commodity": key, "metric": kind, "value": value, "unit": unit,
            "tonnes": tonnes, "kcal": kcal, "is_estimate": is_estimate,
            "phrase": phrase.strip()[:120], "pos": pos,
        })
    return facts


def extract_mentions(ko_text: str = "", en_text: str = "") -> dict[str, int]:
    """Count how many times each commodity is *named* in an article, regardless
    of whether a quantity is attached. This is what lets the analysis surface
    every foodstuff discussed (e.g. barley in a harvest report with no tonnage).

    Detection prefers the English body — English commodity names ("barley",
    "wheat", "grape") are unambiguous as whole words — and falls back to the
    Korean body using only multi-character terms (single-char roots like 밀/벼/배
    are skipped to avoid matching inside unrelated words). Returns
    {commodity_key: count} for every commodity with at least one hit."""
    counts: dict[str, int] = {}
    en = (en_text or "")
    en_low = en.lower()
    ko = ko_text or ""
    for c in D.COMMODITIES:
        n = 0
        if en_low:
            for t in c["en_terms"]:
                t = t.lower().strip()
                if not t:
                    continue
                n += len(re.findall(r'(?<![a-z])' + re.escape(t) + r'(?![a-z])', en_low))
        for t in c["ko"]:
            if len(t) >= 2 and t not in _AMBIGUOUS_KO:   # skip 1-char + ambiguous terms
                n += ko.count(t)
        if n:
            counts[c["key"]] = n
    return counts
