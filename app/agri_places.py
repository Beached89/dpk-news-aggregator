"""Place identification for the place-based agriculture database.

Turns the place names in an article (farms, counties/-gun, cities/-si, provinces,
regions) into normalized keys with an administrative LEVEL and, where known, a
parent PROVINCE and coastal REGION. This is what lets the analysis:
  * de-duplicate the same place reported across multiple articles, and
  * resolve hierarchy (a county sits inside a province) so a province figure and
    its counties' figures aren't double-counted — the most specific wins.

Pure / offline / deterministic — safe to unit-test and to run on every scrape.
"""
from __future__ import annotations

import re

# Administrative levels, broad -> specific. Higher rank = more specific.
LEVEL_RANK = {"country": 0, "region": 1, "province": 2, "city": 3, "county": 4, "farm": 5}

# The nine DPRK provinces (+ the special cities), with romanization variants.
PROVINCES = {
    "south hwanghae": "South Hwanghae", "north hwanghae": "North Hwanghae",
    "south pyongan": "South Pyongan", "north pyongan": "North Pyongan",
    "south hamgyong": "South Hamgyong", "north hamgyong": "North Hamgyong",
    "kangwon": "Kangwon", "gangwon": "Kangwon", "jagang": "Jagang", "chagang": "Jagang",
    "ryanggang": "Ryanggang", "yanggang": "Ryanggang",
    "pyongyang": "Pyongyang", "nampo": "Nampo", "rason": "Rason",
}

# Starter county/city -> province gazetteer (the places that actually show up in
# the farming reports). Editable; unknown counties simply stay parent-less.
COUNTY_PROVINCE = {
    # South Hwanghae
    "jaeryong": "South Hwanghae", "anak": "South Hwanghae", "sinchon": "South Hwanghae",
    "baecheon": "South Hwanghae", "paechon": "South Hwanghae", "yonan": "South Hwanghae",
    "sariwon": "North Hwanghae",
    # North Hwanghae
    "bongsan": "North Hwanghae", "pongsan": "North Hwanghae", "hwangju": "North Hwanghae",
    # North Pyongan
    "kwaksan": "North Pyongan", "gwaksan": "North Pyongan", "seoncheon": "North Pyongan",
    "sonchon": "North Pyongan", "jeongju": "North Pyongan", "jongju": "North Pyongan",
    "yeomju": "North Pyongan", "yomju": "North Pyongan",
    # South Pyongan
    "pyongsong": "South Pyongan", "pyongseong": "South Pyongan", "deokcheon": "South Pyongan",
    "tokchon": "South Pyongan", "sunchon": "South Pyongan", "suncheon": "South Pyongan",
    # South Hamgyong
    "hamju": "South Hamgyong", "jeongpyeong": "South Hamgyong", "jongpyong": "South Hamgyong",
    "geumya": "South Hamgyong", "kumya": "South Hamgyong", "hongwon": "South Hamgyong",
    "hamhung": "South Hamgyong",
}

# Coastal region groupings (the "West Sea" breadbasket vs the eastern provinces).
REGION_OF_PROVINCE = {
    "South Hwanghae": "West Sea", "North Hwanghae": "West Sea",
    "South Pyongan": "West Sea", "North Pyongan": "West Sea",
    "Pyongyang": "West Sea", "Nampo": "West Sea",
    "South Hamgyong": "East Sea", "North Hamgyong": "East Sea",
    "Kangwon": "East Sea", "Rason": "East Sea",
}

_COUNTRY_RE = re.compile(r"\b(the country|nationwide|whole country|across the country|all over the country)\b", re.I)
_PROV_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+Province\b")
_GUN_RE = re.compile(r"\b([A-Z][a-z]+)-?gun\b")
_SI_RE = re.compile(r"\b([A-Z][a-z]+)-?si\b")
_FARM_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+Farm\b")
_REGION_RE = re.compile(r"\b(West Sea|East Sea|eastern region|western region)\b", re.I)


def normalize(name: str) -> str:
    """Lower-case key with admin suffixes/markers stripped, for de-dup matching."""
    s = (name or "").strip().lower()
    s = re.sub(r"[\-\s]?(gun|si|do|province|county|city|farm|군|시|도|농장)\b", "", s)
    s = re.sub(r"[^a-z0-9가-힣]+", " ", s).strip()
    return s


def classify(name: str) -> str:
    """Best-effort administrative level for a raw place string."""
    low = (name or "").lower()
    if _COUNTRY_RE.search(low) or "전국" in name:
        return "country"
    if _REGION_RE.search(name) or "지대" in name:
        return "region"
    if "farm" in low or "농장" in name:
        return "farm"
    if re.search(r"-?gun\b", low) or name.endswith("군"):
        return "county"
    if re.search(r"-?si\b", low) or name.endswith("시"):
        return "city"
    if "province" in low or normalize(name) in PROVINCES or name.endswith("도"):
        return "province"
    return "farm"          # a bare named unit is treated as the most specific


def province_of(name: str, level: str) -> str | None:
    norm = normalize(name)
    if level == "province":
        return PROVINCES.get(norm)
    return COUNTY_PROVINCE.get(norm)


def region_of(name: str, level: str, province: str | None) -> str | None:
    if level == "region":
        m = _REGION_RE.search(name)
        if m:
            t = m.group(1).lower()
            return "West Sea" if "west" in t else "East Sea"
    if province:
        return REGION_OF_PROVINCE.get(province)
    return None


def describe(name: str) -> dict:
    """Full descriptor for one place name."""
    level = classify(name)
    province = province_of(name, level)
    region = region_of(name, level, province)
    return {"name": name.strip(), "norm": normalize(name), "level": level,
            "province": province, "region": region}


def extract_places(text: str) -> list[dict]:
    """Find candidate place names in (English) article text. Returns de-duplicated
    descriptors, most-specific first."""
    if not text:
        return []
    found: dict[str, dict] = {}

    def _add(raw):
        d = describe(raw)
        if d["norm"] and d["norm"] not in found:
            found[d["norm"]] = d

    for m in _FARM_RE.finditer(text):
        _add(m.group(0))
    for m in _GUN_RE.finditer(text):
        _add(m.group(0))
    for m in _SI_RE.finditer(text):
        _add(m.group(0))
    for m in _PROV_RE.finditer(text):
        _add(m.group(0))
    for m in _REGION_RE.finditer(text):
        _add(m.group(1))
    if _COUNTRY_RE.search(text):
        _add("the country")
    return sorted(found.values(), key=lambda d: -LEVEL_RANK.get(d["level"], 0))


def place_positions(text: str) -> list[tuple]:
    """[(char_pos, descriptor), …] for each place mentioned, for nearest-match."""
    out = []
    for d in extract_places(text):
        i = text.find(d["name"])
        if i >= 0:
            out.append((i, d))
    return out


def nearest_place(pos: int, positions: list[tuple], window: int = 400):
    """The place descriptor whose mention is closest to `pos` (within window),
    preferring the more specific one on ties. None if nothing is close."""
    best, bestd = None, window + 1
    for ppos, d in positions:
        dist = abs(ppos - pos)
        if dist < bestd or (dist == bestd and best and
                            LEVEL_RANK.get(d["level"], 0) > LEVEL_RANK.get(best["level"], 0)):
            best, bestd = d, dist
    return best


def contains(parent: dict, child: dict) -> bool:
    """True if `parent` administratively contains `child` (so they overlap and
    only the more specific should be counted). Uses level rank + province/region."""
    if LEVEL_RANK.get(parent["level"], 0) >= LEVEL_RANK.get(child["level"], 0):
        return False
    if parent["level"] == "country":
        return True
    if parent["level"] == "region":
        return parent.get("region") and parent["region"] == child.get("region")
    if parent["level"] == "province":
        return parent.get("province") and parent["province"] == child.get("province")
    return False
