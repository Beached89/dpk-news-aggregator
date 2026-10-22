"""Place-based aggregation engine for the agriculture analysis.

Pure / deterministic. Takes the raw observations (each = one article's claim that
a PLACE produced some tonnage of a COMMODITY, stored as a low–high range) and:

  1. Reconciles repeated reports of the SAME place+commodity into one range
     (manual overrides win; otherwise low = min, high = max — so two articles
     saying 800 t and 1000 t for one farm give 800–1000 t, NOT 1800).
  2. Resolves hierarchy: if a county and the province containing it both report a
     commodity, the broader place is dropped (most-specific wins) so they aren't
     double-counted.
  3. Routes each primary commodity's production through its end-uses:
       food  -> counts as directly-consumable calories
       seed / spoilage(loss) / export / other -> NOT consumable
       process(target) -> becomes a separate product commodity (rice -> rice wine,
                          flour, cake …); its calories count under that product,
                          not as eaten rice
       feed(livestock) -> converted to a livestock product via a feed-conversion
                          ratio; counts under that livestock, not as eaten grain
  4. Computes consumable calories (food buckets only) and per-person/day.

Totals use the LOW end of every range (conservative), and ranges are reported.
"""
from __future__ import annotations

from . import agri_data as D, agri_places as P


def reconcile(observations: list[dict]) -> dict:
    """Group observations by (place, commodity) -> reconciled {low, high, ...}.
    Key: place norm, or a per-article pseudo-key when no place was identified."""
    groups: dict[tuple, list[dict]] = {}
    place_desc: dict[str, dict] = {}
    for o in observations:
        pl = o.get("place")
        if pl and pl.get("norm"):
            pkey = pl["norm"]
            place_desc.setdefault(pkey, pl)
        else:
            pkey = f"__art{o.get('article_id')}"     # unspecified: keep separate
        groups.setdefault((pkey, o["commodity"]), []).append(o)

    reconciled = {}
    for (pkey, commodity), obs in groups.items():
        manual = [o for o in obs if o.get("kind") == "manual"]
        use = manual if manual else obs
        lows = [o["low"] for o in use if o.get("low") is not None]
        highs = [o["high"] for o in use if o.get("high") is not None]
        if not lows and not highs:
            continue
        low = min(lows) if lows else min(highs)
        high = max(highs) if highs else max(lows)
        reconciled[(pkey, commodity)] = {
            "place_key": pkey, "commodity": commodity,
            "low": low, "high": high,
            "manual": bool(manual), "ai": any(o.get("kind") == "ai" for o in use),
            "n": len(obs), "article_ids": sorted({o.get("article_id") for o in obs}),
        }
    return {"reconciled": reconciled, "place_desc": place_desc}


def _resolve_hierarchy(reconciled: dict, place_desc: dict) -> dict:
    """Drop broader places when a more-specific contained place reports the same
    commodity (prefer most specific). Returns the kept reconciled entries."""
    by_commodity: dict[str, list[tuple]] = {}
    for (pkey, commodity), rec in reconciled.items():
        by_commodity.setdefault(commodity, []).append((pkey, rec))

    dropped: set[tuple] = set()
    for commodity, entries in by_commodity.items():
        for pk_a, _ in entries:
            for pk_b, _ in entries:
                if pk_a == pk_b:
                    continue
                da, db = place_desc.get(pk_a), place_desc.get(pk_b)
                if not da or not db:
                    continue                     # unspecified places never merge
                if P.contains(da, db):           # a (broader) contains b -> drop a
                    dropped.add((pk_a, commodity))
    return {k: v for k, v in reconciled.items() if k not in dropped}


def _meta(key: str, commodity_meta: dict) -> dict:
    """Effective commodity metadata: DB/AI overlay first, else the built-in
    default, else a bare fallback so unknown keys still resolve."""
    if commodity_meta and key in commodity_meta:
        return commodity_meta[key]
    c = D.COMMODITY_BY_KEY.get(key)
    if c:
        return {"en": c["en"], "cat": c["cat"], "kind": c["kind"], "kcal_per_kg": c["kcal_per_kg"]}
    return {"en": key.replace("_", " ").title(), "cat": "", "kind": "processed", "kcal_per_kg": 0}


def _entries(key: str, enduse_map: dict) -> list[dict]:
    """Effective end-use entries [{label,frac,type,target,yield?,fcr?}]: AI/DB
    overlay first, else the built-in default split."""
    if enduse_map and key in enduse_map:
        return enduse_map[key]
    return D.enduse_entries(key)


def compute(observations: list[dict], commodity_meta: dict | None = None,
            enduse_map: dict | None = None) -> dict:
    """Full aggregation. End-use splits and commodity metadata come from the
    passed overlays (DB/AI-discovered) when present, else the built-in defaults.
    Production is routed RECURSIVELY through end-uses (rice -> rice wine, etc.,
    and onward) so the calorie model follows the whole food chain. Returns
    per-commodity production + routing + the consumable-calorie totals."""
    r = reconcile(observations)
    kept = _resolve_hierarchy(r["reconciled"], r["place_desc"])

    base_low: dict[str, float] = {}
    base_high: dict[str, float] = {}
    has_ai: dict[str, bool] = {}
    for (_pkey, commodity), rec in kept.items():
        base_low[commodity] = base_low.get(commodity, 0.0) + rec["low"]
        base_high[commodity] = base_high.get(commodity, 0.0) + rec["high"]
        has_ai[commodity] = has_ai.get(commodity, False) or rec["ai"]

    def _route_one(key, amt, food_t, received, max_depth, _depth=0):
        """Distribute `amt` tonnes that flow INTO `key` across its end-uses,
        recursing into process/feed products. Depth-capped to stay finite."""
        received[key] = received.get(key, 0.0) + amt
        entries = _entries(key, enduse_map)
        if not entries or _depth > max_depth:
            food_t[key] = food_t.get(key, 0.0) + amt   # no end-use info -> eaten
            return
        for e in entries:
            portion = amt * (e.get("frac") or 0.0)
            t = e.get("type")
            if t == "food":
                food_t[key] = food_t.get(key, 0.0) + portion
            elif t == "process" and e.get("target"):
                y = e.get("yield") or D.PROCESS_YIELD.get(e["target"], D.PROCESS_YIELD_DEFAULT)
                _route_one(e["target"], portion * y, food_t, received, max_depth, _depth + 1)
            elif t == "feed":
                tgt = e.get("target") or D.DEFAULT_FEED_TARGET
                fcr = e.get("fcr") or D.FEED_FCR.get(tgt, D.FEED_FCR_DEFAULT)
                if fcr:
                    _route_one(tgt, portion / fcr, food_t, received, max_depth, _depth + 1)
            # seed / loss / export / other -> not consumable, flow stops

    def _distribute(base):
        food_t, received = {}, {}
        for key, amt in base.items():
            _route_one(key, amt, food_t, received, max_depth=8)
        return food_t, received

    food_low, recv_low = _distribute(base_low)
    _, recv_high = _distribute(base_high)

    all_keys = set(base_low) | set(base_high) | set(recv_low) | set(recv_high)
    commodities = {}
    total_food_kcal = 0.0
    for key in all_keys:
        m = _meta(key, commodity_meta)
        kcal_kg = m.get("kcal_per_kg") or 0
        food_tonnes = food_low.get(key, 0.0)
        kcal = food_tonnes * 1000 * kcal_kg
        total_food_kcal += kcal
        b_low, b_high = base_low.get(key, 0.0), base_high.get(key, 0.0)
        derived_in = max(recv_low.get(key, 0.0) - b_low, 0.0)
        # End-use rows for display, based on everything that flowed through `key`.
        through = recv_low.get(key, 0.0)
        rows = []
        for e in _entries(key, enduse_map):
            raw = through * (e.get("frac") or 0.0)
            row = {"label": e.get("label"), "frac": e.get("frac") or 0.0,
                   "type": e.get("type"), "target": e.get("target"), "product_tonnes": None}
            if e.get("type") == "process" and e.get("target"):
                y = e.get("yield") or D.PROCESS_YIELD.get(e["target"], D.PROCESS_YIELD_DEFAULT)
                row["product_tonnes"] = raw * y
            elif e.get("type") == "feed":
                tgt = e.get("target") or D.DEFAULT_FEED_TARGET
                fcr = e.get("fcr") or D.FEED_FCR.get(tgt, D.FEED_FCR_DEFAULT)
                row["target"] = tgt
                row["product_tonnes"] = (raw / fcr) if fcr else 0.0
            row["raw_tonnes"] = raw
            rows.append(row)
        commodities[key] = {
            "key": key, "en": m.get("en") or key.title(), "cat": m.get("cat") or "",
            "kind": m.get("kind") or "primary",
            "base_low": b_low, "base_high": b_high, "derived_in": derived_in,
            "total_low": recv_low.get(key, 0.0), "total_high": recv_high.get(key, 0.0),
            "food_tonnes": food_tonnes, "kcal": kcal, "ai": has_ai.get(key, False),
            "routes": rows,
        }

    pop = D.POPULATION
    per_capita_day = (total_food_kcal / pop / 365.0) if pop else 0.0
    prod_low = sum(base_low.values())
    prod_high = sum(base_high.values())
    return {
        "commodities": commodities,
        "total_food_kcal": total_food_kcal,
        "per_capita_day": per_capita_day,
        "prod_low": prod_low, "prod_high": prod_high,
        "population": pop,
    }
