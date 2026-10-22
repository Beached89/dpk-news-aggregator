"""Copy-paste AI quantification helpers (no API, no key).

This module does NOT call any AI service. It only:
  * builds a research prompt to paste into a chat assistant that has web access
    (Claude.ai, Gemini, …), and
  * parses the JSON answer the user pastes back.

The assistant does the research (look up the places named, provincial cultivated
area, regional yields, planting techniques) and returns a tonnage estimate; we
just record it. Estimates are flagged is_ai and shown with a "*".
"""
from __future__ import annotations

import json
import re

_MAX_CHARS = 6000   # cap article text included in the prompt

_SYSTEM = (
    "You are an agricultural-analysis researcher estimating output from North "
    "Korean (DPRK) state-media articles. The reporting is qualitative and "
    "propagandistic, so you must RESEARCH to turn it into a quantity.\n\n"
    "Goal: for the ONE commodity given, estimate the total quantity described in "
    "the article, in metric tonnes, by reconstructing (a) the land area planted "
    "or the herd/catch size and (b) the expected or historical yield.\n\n"
    "If you have web search / browsing available, USE IT AGGRESSIVELY — search the "
    "whole web, not just the article. Research, in order:\n"
    "1. Identify every place named. When a SPECIFIC facility is named (a named "
    "farm, co-op, fish/aquaculture center, orchard, or plant — e.g. 'Sinpo City "
    "Sea Aquaculture Business Center'), research THAT facility by name: its "
    "documented size/area, number of cages/tanks/ponds/greenhouses, the "
    "technology it uses, when it was built/expanded, satellite or news coverage, "
    "and any previously published output or capacity. Base the estimate on the "
    "facility itself; fall back to regional averages only for what you can't find.\n"
    "2. Otherwise look up the cultivated area / herd / catch for this commodity in "
    "the named administrative areas — from FAO, USDA FAS, DPRK statistics, KREI, "
    "UN/NGO assessments, NK Pro/38 North, or prior reporting on the same place — "
    "and how much of it this article's described activity plausibly covers.\n"
    "3. Look up the representative YIELD (t/ha) for this commodity in that region, "
    "adjusting for soil fertility and the planting techniques mentioned "
    "(e.g. machine transplanting, Juche farming method, seedling raising).\n"
    "4. Cross-check against any numbers in the article (정보/hectares, head counts, "
    "counts of farms/provinces, year-on-year comparisons).\n"
    "Note: you cannot measure area off live satellite imagery — rely on PUBLISHED "
    "area figures and statistics, not pixel measurement.\n\n"
    "Then compute tonnes = planted_area_ha × yield_t_ha (or herd/catch × unit "
    "weight). Be deliberately CONSERVATIVE — when sources conflict, choose the "
    "lower figure. If after researching there is genuinely no basis to bound a "
    "quantity, return null for tonnes. Show your derivation and cite sources."
)

_SCHEMA_HINT = (
    '{"place": "<the single most-specific place this estimate is for, e.g. '
    '\'Jaeryong-gun\' or \'South Hwanghae Province\'; null if none named>", '
    '"planted_area_ha": <number or null>, "yield_t_ha": <number or null>, '
    '"spoilage_pct": <number or null: estimated post-harvest loss %>, '
    '"low_tonnes": <number or null>, "high_tonnes": <number or null>, '
    '"confidence": "low|medium|high", '
    '"basis": "<2-3 sentences: the place identified, the area, yield and spoilage '
    'you researched, and how you combined them>", '
    '"sources": ["<source name or URL>", "..."]}'
)


def _user_prompt(commodity_en: str, title: str, body: str) -> str:
    body = (body or "")[:_MAX_CHARS]
    return (
        f"Commodity to quantify: {commodity_en}\n\n"
        f"Article title: {title or '(none)'}\n\n"
        f"Article text:\n{body}\n\n"
        f"Research the places and figures as instructed, then return JSON exactly "
        f"in this shape: {_SCHEMA_HINT}"
    )


def build_manual_prompt(commodity_en: str, title: str, body: str) -> str:
    """A single self-contained research prompt to paste into a chat assistant
    that has web access (Claude.ai, Gemini, …)."""
    return (
        f"{_SYSTEM}\n\n"
        f"{_user_prompt(commodity_en, title, body)}\n\n"
        f"Use your web-search tool if you have one. Respond with ONLY the JSON "
        f"object on its own — no explanation outside it, no code fence."
    )


def parse_result(text: str) -> dict | None:
    """Normalize the JSON the user pasted into {tonnes, confidence, basis}.
    Optional research fields (planted_area_ha, yield_t_ha, sources) are folded
    into the basis so they show up in the stored note. None if unparseable."""
    parsed = _parse_json(text or "")
    if parsed is None:
        return None

    def _num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # Accept either an explicit low/high range or a single tonnes value.
    low = _num(parsed.get("low_tonnes"))
    high = _num(parsed.get("high_tonnes"))
    single = _num(parsed.get("tonnes"))
    if low is None and high is None and single is not None:
        low = high = single
    if low is not None and high is None:
        high = low
    if high is not None and low is None:
        low = high
    if low is not None and low <= 0:
        low = None
    if high is not None and high <= 0:
        high = None
    if low is not None and high is not None and low > high:
        low, high = high, low

    area = _num(parsed.get("planted_area_ha"))
    yld = _num(parsed.get("yield_t_ha"))
    spoil = _num(parsed.get("spoilage_pct"))
    place = parsed.get("place")
    place = str(place).strip() if place else None

    basis = str(parsed.get("basis", "") or "").strip()
    bits = []
    if area:
        bits.append(f"~{area:,.0f} ha")
    if yld:
        bits.append(f"~{yld:g} t/ha")
    if spoil:
        bits.append(f"spoilage ~{spoil:g}%")
    srcs = parsed.get("sources")
    if isinstance(srcs, list) and srcs:
        bits.append("sources: " + ", ".join(str(x) for x in srcs[:4]))
    elif isinstance(srcs, str) and srcs.strip():
        bits.append("sources: " + srcs.strip())
    if bits:
        basis = (basis + "  [" + "; ".join(bits) + "]").strip()

    return {
        "place": place, "low": low, "high": high,
        "tonnes": low,                       # back-compat / convenience
        "confidence": str(parsed.get("confidence", "") or "")[:12],
        "basis": basis[:480],
    }


_ENDUSE_SYSTEM = (
    "You are a food-systems researcher mapping how North Korea (DPRK) actually "
    "USES a given agricultural commodity. Research and return the COMPLETE set of "
    "end-uses for it — not just the obvious ones — with the fraction of total "
    "output going to each (fractions should sum to ~1.0). Be exhaustive: direct "
    "consumption, seed, post-harvest loss/spoilage, animal feed, export, and EVERY "
    "processed product it's turned into (flours, oils, starches, drinks, alcohols, "
    "confections, pastes/sauces, noodles, cakes, by-products like bran or husk, "
    "industrial uses, etc.). For each processed product, RECURSE: give that "
    "product its own end-uses too, as deeply as is meaningful.\n\n"
    "Classify each use's `type` as exactly one of: food (eaten directly as this "
    "commodity), seed, loss (spoilage), feed (to livestock), process (made into "
    "another product), export, other. For `process` give the product with a "
    "snake_case `key`, a display `name`, and `kcal_per_kg`. For `feed` give the "
    "`livestock` key (e.g. pork, chicken, goat, beef, fish). Be conservative and "
    "grounded; use web search if available. Respond with ONLY the JSON object."
)

_ENDUSE_SCHEMA = (
    '{"commodity":"<key>","end_uses":['
    '{"use":"<label>","fraction":<0..1>,"type":"food|seed|loss|feed|process|export|other",'
    '"livestock":"<key, only if type=feed>",'
    '"product":{"key":"<snake_case>","name":"<display>","kcal_per_kg":<number>,'
    '"end_uses":[ ...same shape, recursively... ]}}'
    ']}'
)


def build_enduse_prompt(commodity_key: str, commodity_en: str) -> str:
    """Prompt to paste into a web-capable assistant to discover a commodity's
    full (recursive) end-use breakdown."""
    return (
        f"{_ENDUSE_SYSTEM}\n\n"
        f"Commodity: {commodity_en}  (key: {commodity_key})\n\n"
        f"Use your web-search tool if you have one. Return ONLY this JSON shape "
        f"(recurse `product.end_uses` for major processed products):\n{_ENDUSE_SCHEMA}"
    )


_VALID_TYPES = {"food", "seed", "loss", "feed", "process", "export", "other"}


def parse_enduse(text: str) -> dict | None:
    """Parse a pasted end-use discovery answer into a normalized nested dict:
    {end_uses:[{label,frac,type,target,product?,livestock?}]}. None if unparseable."""
    parsed = _parse_json(text or "")
    if not parsed or not isinstance(parsed.get("end_uses"), list):
        return None

    def _norm_list(items):
        out = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            try:
                frac = float(it.get("fraction"))
            except (TypeError, ValueError):
                continue
            if frac <= 0:
                continue
            utype = str(it.get("type", "other")).strip().lower()
            if utype not in _VALID_TYPES:
                utype = "other"
            entry = {"label": str(it.get("use") or utype)[:80], "frac": frac,
                     "type": utype, "target": None, "product": None}
            if utype == "feed" and it.get("livestock"):
                entry["target"] = str(it["livestock"]).strip().lower()[:48]
            if utype == "process" and isinstance(it.get("product"), dict):
                p = it["product"]
                key = str(p.get("key") or "").strip().lower().replace(" ", "_")[:48]
                if key:
                    entry["target"] = key
                    try:
                        kcal = float(p.get("kcal_per_kg") or 0)
                    except (TypeError, ValueError):
                        kcal = 0.0
                    entry["product"] = {"key": key, "name": str(p.get("name") or key)[:96],
                                        "kcal_per_kg": kcal,
                                        "end_uses": _norm_list(p.get("end_uses"))}
            out.append(entry)
        return out

    entries = _norm_list(parsed["end_uses"])
    return {"end_uses": entries} if entries else None


def _parse_json(text: str) -> dict | None:
    """Pull the first JSON object out of the pasted reply (tolerant of code
    fences or stray prose around it)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None
