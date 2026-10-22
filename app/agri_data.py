"""Reference data for the agriculture analysis — researched estimates, editable.

Everything here is an ESTIMATE used to quantify vague reporting:
  * yield_t_ha   — representative average yield (t/ha). The pipeline multiplies
                   by YIELD_FACTOR (0.75) to be conservative, per spec.
  * kcal_per_kg  — edible energy per kg.
  * enduse       — typical utilisation split (fractions sum ~1.0).
Figures are rough public-data averages, NOT measured DPRK data. Tune freely.

Each commodity has a `kind`:
  * "primary"   — a crop/livestock/fishery product *grown or raised in the DPRK*.
                  Counts toward total food produced and the calorie tally.
  * "processed" — a manufactured food made *from* other items (flour from wheat,
                  soy sauce from soybeans, juice from fruit, bread, candy, …).
                  Tracked and quantified, but NOT added to the production /
                  calorie totals — its calories are already counted in the raw
                  crop it's made from, so adding it would double-count. This is
                  the "food processing" layer.
"""
from __future__ import annotations

POPULATION = 26_000_000          # DPRK, approx — for calories/person
YIELD_FACTOR = 0.75              # use 75% of average yield when estimating tonnage

# Fuzzy quantifiers -> a representative number. Ordered: match larger first.
FUZZY_QUANTIFIERS = [
    ("수십만", 300_000), ("수십 만", 300_000),
    ("수만", 30_000), ("수 만", 30_000),
    ("수천", 3_000), ("수 천", 3_000),
    ("수백", 300), ("수 백", 300),
    ("수십", 50), ("수 십", 50),
    ("여러", 5), ("수많은", 100),
    ("hundreds of thousands", 300_000),
    ("tens of thousands", 30_000),
    ("thousands of", 3_000),
    ("hundreds of", 300),
    ("scores of", 60),
    ("dozens of", 36),
    ("several", 5),
]

# Korean digit-attached multipliers (e.g. "5천" -> 5000, "1만" -> 10000).
KO_MULTIPLIERS = {"십": 10, "백": 100, "천": 1_000, "만": 10_000, "억": 100_000_000}

# Units -> (kind, factor to base). Base: area=hectares, mass=tonnes, count=head.
UNITS = [
    ("정보", "area", 1.0), ("헥타르", "area", 1.0), ("ha", "area", 1.0),
    ("hectares", "area", 1.0), ("hectare", "area", 1.0),
    ("acres", "area", 0.404686), ("acre", "area", 0.404686), ("에이커", "area", 0.404686),
    ("톤", "mass", 1.0), ("tonnes", "mass", 1.0), ("tonne", "mass", 1.0),
    ("tons", "mass", 1.0), ("ton", "mass", 1.0),
    ("kg", "mass", 0.001), ("키로그람", "mass", 0.001), ("킬로그람", "mass", 0.001),
    ("마리", "count", 1.0), ("head", "count", 1.0),
]

# ---------------------------------------------------------------------------
# Commodities. Fields: key, en (display), cat, kind ("primary"|"processed"),
# ko (terms — avoid bare 1-char roots; mention detection skips <2 chars),
# en_terms (matched as whole words), yield_t_ha (None = not area-estimable:
# livestock, fishery, processed), kcal_per_kg, enduse split.
# ---------------------------------------------------------------------------
COMMODITIES = [
    # ===================== PRIMARY: grains =====================
    {"key": "rice", "en": "Rice", "cat": "grain", "kind": "primary",
     "ko": ["벼", "쌀", "입쌀", "정조", "논벼", "벼농사"], "en_terms": ["rice", "paddy"],
     "yield_t_ha": 4.5, "kcal_per_kg": 3600,
     "enduse": {"food": 0.62, "seed": 0.05, "spoilage": 0.13, "rice flour": 0.06,
                "rice cake": 0.05, "rice wine": 0.04, "fodder": 0.05}},
    {"key": "maize", "en": "Maize (corn)", "cat": "grain", "kind": "primary",
     "ko": ["강냉이", "옥수수"], "en_terms": ["maize", "corn"],
     "yield_t_ha": 5.0, "kcal_per_kg": 3650,
     "enduse": {"food": 0.40, "fodder": 0.50, "seed/other": 0.10}},
    {"key": "wheat", "en": "Wheat", "cat": "grain", "kind": "primary",
     "ko": ["밀농사", "밀재배", "밀밭", "밀가을", "밀수확"], "en_terms": ["wheat"],
     "yield_t_ha": 3.0, "kcal_per_kg": 3400,
     "enduse": {"food": 0.85, "seed": 0.05, "fodder/other": 0.10}},
    {"key": "barley", "en": "Barley", "cat": "grain", "kind": "primary",
     "ko": ["보리"], "en_terms": ["barley"],
     "yield_t_ha": 3.0, "kcal_per_kg": 3300,
     "enduse": {"food": 0.60, "fodder": 0.30, "seed/other": 0.10}},
    {"key": "millet", "en": "Millet / sorghum", "cat": "grain", "kind": "primary",
     "ko": ["기장", "수수", "조이삭"], "en_terms": ["millet", "sorghum"],
     "yield_t_ha": 2.5, "kcal_per_kg": 3780,
     "enduse": {"food": 0.70, "fodder": 0.20, "seed/other": 0.10}},
    {"key": "buckwheat", "en": "Buckwheat", "cat": "grain", "kind": "primary",
     "ko": ["메밀"], "en_terms": ["buckwheat"],
     "yield_t_ha": 1.2, "kcal_per_kg": 3430,
     "enduse": {"food": 0.90, "seed/other": 0.10}},
    {"key": "oat", "en": "Oat / rye", "cat": "grain", "kind": "primary",
     "ko": ["귀리", "호밀"], "en_terms": ["oat", "rye"],
     "yield_t_ha": 2.5, "kcal_per_kg": 3500,
     "enduse": {"food": 0.55, "fodder": 0.35, "seed/other": 0.10}},
    {"key": "grain", "en": "Grain (unspecified)", "cat": "grain", "kind": "primary",
     "ko": ["알곡", "곡물", "낟알"], "en_terms": ["grain", "cereal", "cereals"],
     "yield_t_ha": 4.0, "kcal_per_kg": 3450,
     "enduse": {"food": 0.80, "seed": 0.05, "fodder/other": 0.15}},

    # ===================== PRIMARY: pulses / oilseeds / tubers =====================
    {"key": "soybean", "en": "Soybean", "cat": "oilseed", "kind": "primary",
     "ko": ["콩", "대두", "콩농사"], "en_terms": ["soybean", "soya", "soy bean"],
     "yield_t_ha": 1.8, "kcal_per_kg": 4460,
     "enduse": {"food": 0.55, "oil": 0.30, "fodder/other": 0.15}},
    {"key": "red_bean", "en": "Red bean / pulses", "cat": "pulse", "kind": "primary",
     "ko": ["팥", "녹두", "강낭콩"], "en_terms": ["red bean", "adzuki", "mung bean", "pulse"],
     "yield_t_ha": 1.5, "kcal_per_kg": 3290,
     "enduse": {"food": 0.90, "seed/other": 0.10}},
    {"key": "groundnut", "en": "Groundnut / peanut", "cat": "oilseed", "kind": "primary",
     "ko": ["락화생", "땅콩"], "en_terms": ["groundnut", "peanut"],
     "yield_t_ha": 1.5, "kcal_per_kg": 5670,
     "enduse": {"food": 0.50, "oil": 0.40, "seed/other": 0.10}},
    {"key": "sesame", "en": "Sesame / perilla", "cat": "oilseed", "kind": "primary",
     "ko": ["참깨", "들깨", "깨농사"], "en_terms": ["sesame", "perilla"],
     "yield_t_ha": 0.6, "kcal_per_kg": 5730,
     "enduse": {"oil": 0.70, "food": 0.30}},
    {"key": "rapeseed", "en": "Rapeseed / canola", "cat": "oilseed", "kind": "primary",
     "ko": ["유채"], "en_terms": ["rapeseed", "canola"],
     "yield_t_ha": 1.5, "kcal_per_kg": 4900,
     "enduse": {"oil": 0.80, "fodder/other": 0.20}},
    {"key": "potato", "en": "Potato", "cat": "tuber", "kind": "primary",
     "ko": ["감자"], "en_terms": ["potato"],
     "yield_t_ha": 18.0, "kcal_per_kg": 770,
     "enduse": {"food": 0.70, "seed": 0.15, "fodder": 0.10, "processing": 0.05}},
    {"key": "sweet_potato", "en": "Sweet potato", "cat": "tuber", "kind": "primary",
     "ko": ["고구마"], "en_terms": ["sweet potato"],
     "yield_t_ha": 15.0, "kcal_per_kg": 860,
     "enduse": {"food": 0.70, "fodder": 0.20, "processing": 0.10}},

    # ===================== PRIMARY: vegetables =====================
    {"key": "cabbage", "en": "Cabbage", "cat": "vegetable", "kind": "primary",
     "ko": ["배추"], "en_terms": ["cabbage"],
     "yield_t_ha": 30.0, "kcal_per_kg": 250,
     "enduse": {"food": 0.80, "kimchi/processing": 0.20}},
    {"key": "radish", "en": "Radish", "cat": "vegetable", "kind": "primary",
     "ko": ["무우", "왜무", "무김치"], "en_terms": ["radish"],
     "yield_t_ha": 35.0, "kcal_per_kg": 180,
     "enduse": {"food": 0.80, "kimchi/processing": 0.20}},
    {"key": "tomato", "en": "Tomato", "cat": "vegetable", "kind": "primary",
     "ko": ["도마도", "토마토"], "en_terms": ["tomato"],
     "yield_t_ha": 30.0, "kcal_per_kg": 180,
     "enduse": {"food": 0.85, "processing": 0.15}},
    {"key": "cucumber", "en": "Cucumber", "cat": "vegetable", "kind": "primary",
     "ko": ["오이"], "en_terms": ["cucumber"],
     "yield_t_ha": 30.0, "kcal_per_kg": 150,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "garlic", "en": "Garlic", "cat": "vegetable", "kind": "primary",
     "ko": ["마늘"], "en_terms": ["garlic"],
     "yield_t_ha": 12.0, "kcal_per_kg": 1490,
     "enduse": {"food": 0.90, "processing": 0.10}},
    {"key": "onion", "en": "Onion", "cat": "vegetable", "kind": "primary",
     "ko": ["양파", "대파"], "en_terms": ["onion"],
     "yield_t_ha": 35.0, "kcal_per_kg": 400,
     "enduse": {"food": 0.90, "processing": 0.10}},
    {"key": "pepper", "en": "Red pepper / chili", "cat": "vegetable", "kind": "primary",
     "ko": ["고추"], "en_terms": ["red pepper", "chili", "chilli", "pepper"],
     "yield_t_ha": 3.0, "kcal_per_kg": 400,
     "enduse": {"food": 0.80, "processing": 0.20}},
    {"key": "pumpkin", "en": "Pumpkin / squash", "cat": "vegetable", "kind": "primary",
     "ko": ["호박"], "en_terms": ["pumpkin", "squash"],
     "yield_t_ha": 25.0, "kcal_per_kg": 260,
     "enduse": {"food": 0.85, "fodder": 0.10, "processing": 0.05}},
    {"key": "eggplant", "en": "Eggplant", "cat": "vegetable", "kind": "primary",
     "ko": ["가지"], "en_terms": ["eggplant", "aubergine"],
     "yield_t_ha": 28.0, "kcal_per_kg": 250,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "carrot", "en": "Carrot", "cat": "vegetable", "kind": "primary",
     "ko": ["당근"], "en_terms": ["carrot"],
     "yield_t_ha": 30.0, "kcal_per_kg": 410,
     "enduse": {"food": 0.90, "processing": 0.10}},
    {"key": "spinach", "en": "Spinach / leafy greens", "cat": "vegetable", "kind": "primary",
     "ko": ["시금치", "상추", "부루"], "en_terms": ["spinach", "lettuce"],
     "yield_t_ha": 20.0, "kcal_per_kg": 200,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "vegetable", "en": "Vegetables (other)", "cat": "vegetable", "kind": "primary",
     "ko": ["남새", "채소", "야채"], "en_terms": ["vegetable", "greens"],
     "yield_t_ha": 25.0, "kcal_per_kg": 300,
     "enduse": {"food": 0.85, "processing": 0.15}},
    {"key": "mushroom", "en": "Mushroom", "cat": "vegetable", "kind": "primary",
     "ko": ["버섯", "송이버섯", "느타리"], "en_terms": ["mushroom"],
     "yield_t_ha": None, "kcal_per_kg": 250,
     "enduse": {"food": 0.85, "export/processing": 0.15}},

    # ===================== PRIMARY: fruit / nuts =====================
    {"key": "apple", "en": "Apple", "cat": "fruit", "kind": "primary",
     "ko": ["사과", "능금"], "en_terms": ["apple"],
     "yield_t_ha": 12.0, "kcal_per_kg": 520,
     "enduse": {"food": 0.55, "juice": 0.25, "jam/processed": 0.10, "fodder": 0.10}},
    {"key": "pear", "en": "Pear", "cat": "fruit", "kind": "primary",
     "ko": ["배나무", "배밭", "배과수"], "en_terms": ["pear"],
     "yield_t_ha": 12.0, "kcal_per_kg": 570,
     "enduse": {"food": 0.65, "juice": 0.25, "processing": 0.10}},
    {"key": "persimmon", "en": "Persimmon", "cat": "fruit", "kind": "primary",
     "ko": ["감나무", "단감", "곶감"], "en_terms": ["persimmon"],
     "yield_t_ha": 10.0, "kcal_per_kg": 700,
     "enduse": {"food": 0.70, "dried/processed": 0.30}},
    {"key": "peach", "en": "Peach", "cat": "fruit", "kind": "primary",
     "ko": ["복숭아"], "en_terms": ["peach"],
     "yield_t_ha": 11.0, "kcal_per_kg": 390,
     "enduse": {"food": 0.80, "processing": 0.20}},
    {"key": "apricot", "en": "Apricot", "cat": "fruit", "kind": "primary",
     "ko": ["살구"], "en_terms": ["apricot"],
     "yield_t_ha": 8.0, "kcal_per_kg": 480,
     "enduse": {"food": 0.80, "dried/processed": 0.20}},
    {"key": "plum", "en": "Plum / cherry", "cat": "fruit", "kind": "primary",
     "ko": ["자두", "오얏", "버찌", "앵두"], "en_terms": ["plum", "cherry"],
     "yield_t_ha": 9.0, "kcal_per_kg": 460,
     "enduse": {"food": 0.85, "processing": 0.15}},
    {"key": "grape", "en": "Grape", "cat": "fruit", "kind": "primary",
     "ko": ["포도"], "en_terms": ["grape"],
     "yield_t_ha": 12.0, "kcal_per_kg": 690,
     "enduse": {"food": 0.70, "juice/wine": 0.30}},
    {"key": "jujube", "en": "Jujube / date", "cat": "fruit", "kind": "primary",
     "ko": ["대추"], "en_terms": ["jujube"],
     "yield_t_ha": 6.0, "kcal_per_kg": 2870,
     "enduse": {"food": 0.70, "dried/processed": 0.30}},
    {"key": "strawberry", "en": "Strawberry / berries", "cat": "fruit", "kind": "primary",
     "ko": ["딸기"], "en_terms": ["strawberry", "berry"],
     "yield_t_ha": 20.0, "kcal_per_kg": 320,
     "enduse": {"food": 0.85, "jam/processed": 0.15}},
    {"key": "melon", "en": "Melon / watermelon", "cat": "fruit", "kind": "primary",
     "ko": ["수박", "참외", "메론"], "en_terms": ["melon", "watermelon"],
     "yield_t_ha": 25.0, "kcal_per_kg": 300,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "fruit", "en": "Fruit (other)", "cat": "fruit", "kind": "primary",
     "ko": ["과일", "과수", "과수원", "실과"], "en_terms": ["fruit", "orchard"],
     "yield_t_ha": 11.0, "kcal_per_kg": 520,
     "enduse": {"food": 0.70, "juice": 0.20, "processing": 0.10}},
    {"key": "chestnut", "en": "Chestnut / nuts", "cat": "nut", "kind": "primary",
     "ko": ["알밤", "밤나무", "호두", "잣"], "en_terms": ["chestnut", "walnut", "pine nut"],
     "yield_t_ha": 2.0, "kcal_per_kg": 2130,
     "enduse": {"food": 0.90, "processing": 0.10}},

    # ===================== PRIMARY: livestock =====================
    {"key": "pork", "en": "Pork / meat", "cat": "livestock", "kind": "primary",
     "ko": ["돼지", "돼지고기", "축산물", "고기"], "en_terms": ["pork", "meat", "livestock"],
     "yield_t_ha": None, "kcal_per_kg": 2420,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "beef", "en": "Beef / cattle", "cat": "livestock", "kind": "primary",
     "ko": ["소고기", "쇠고기", "육우", "황소"], "en_terms": ["beef", "cattle"],
     "yield_t_ha": None, "kcal_per_kg": 2500,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "chicken", "en": "Chicken / poultry", "cat": "livestock", "kind": "primary",
     "ko": ["닭고기", "육계", "닭공장", "양계"], "en_terms": ["chicken", "poultry", "broiler"],
     "yield_t_ha": None, "kcal_per_kg": 2150,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "duck", "en": "Duck", "cat": "livestock", "kind": "primary",
     "ko": ["오리고기", "집오리", "오리공장"], "en_terms": ["duck"],
     "yield_t_ha": None, "kcal_per_kg": 2400,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "goat", "en": "Goat / sheep", "cat": "livestock", "kind": "primary",
     "ko": ["염소", "양고기", "면양"], "en_terms": ["goat", "mutton", "sheep"],
     "yield_t_ha": None, "kcal_per_kg": 1450,
     "enduse": {"food": 0.95, "processing": 0.05}},
    {"key": "rabbit", "en": "Rabbit", "cat": "livestock", "kind": "primary",
     "ko": ["토끼", "집토끼"], "en_terms": ["rabbit"],
     "yield_t_ha": None, "kcal_per_kg": 1730,
     "enduse": {"food": 0.90, "processing": 0.10}},
    {"key": "egg", "en": "Eggs", "cat": "livestock", "kind": "primary",
     "ko": ["닭알", "계란", "오리알", "알생산"], "en_terms": ["egg"],
     "yield_t_ha": None, "kcal_per_kg": 1430,
     "enduse": {"food": 0.97, "other": 0.03}},
    {"key": "milk", "en": "Milk", "cat": "livestock", "kind": "primary",
     "ko": ["우유", "젖소", "소젖", "염소젖"], "en_terms": ["milk", "raw milk"],
     "yield_t_ha": None, "kcal_per_kg": 600,
     "enduse": {"food": 0.90, "processing": 0.10}},
    {"key": "honey", "en": "Honey", "cat": "livestock", "kind": "primary",
     "ko": ["벌꿀", "양봉", "꿀생산"], "en_terms": ["honey", "apiculture"],
     "yield_t_ha": None, "kcal_per_kg": 3040,
     "enduse": {"food": 0.90, "processing": 0.10}},

    # ===================== PRIMARY: fishery =====================
    {"key": "fish", "en": "Fish / aquatic", "cat": "fishery", "kind": "primary",
     "ko": ["물고기", "수산물", "수산업", "어업", "어로", "양어", "양식어"],
     "en_terms": ["fish", "aquatic product", "fishery"],
     "yield_t_ha": None, "kcal_per_kg": 1100,
     "enduse": {"food": 0.85, "processing": 0.15}},
    {"key": "shellfish", "en": "Shellfish", "cat": "fishery", "kind": "primary",
     "ko": ["조개", "굴", "홍합", "전복"], "en_terms": ["shellfish", "oyster", "clam", "abalone"],
     "yield_t_ha": None, "kcal_per_kg": 850,
     "enduse": {"food": 0.85, "processing": 0.15}},
    {"key": "shrimp_crab", "en": "Shrimp / crab", "cat": "fishery", "kind": "primary",
     "ko": ["새우", "게", "젓새우"], "en_terms": ["shrimp", "prawn", "crab"],
     "yield_t_ha": None, "kcal_per_kg": 950,
     "enduse": {"food": 0.85, "processing": 0.15}},
    {"key": "seaweed", "en": "Seaweed / kelp", "cat": "fishery", "kind": "primary",
     "ko": ["다시마", "미역", "김양식", "바다나물"], "en_terms": ["seaweed", "kelp", "laver"],
     "yield_t_ha": None, "kcal_per_kg": 450,
     "enduse": {"food": 0.80, "processing": 0.20}},

    # ===================== PRIMARY: other crops =====================
    {"key": "ginseng", "en": "Ginseng", "cat": "industrial crop", "kind": "primary",
     "ko": ["인삼", "고려인삼"], "en_terms": ["ginseng"],
     "yield_t_ha": None, "kcal_per_kg": 400,
     "enduse": {"processing": 0.60, "export": 0.40}},
    {"key": "salt", "en": "Salt", "cat": "mineral", "kind": "primary",
     "ko": ["소금", "제염", "소금밭", "천일염"], "en_terms": ["salt"],
     "yield_t_ha": None, "kcal_per_kg": 0,
     "enduse": {"food": 0.60, "processing/industry": 0.40}},

    # ===================== PROCESSED: foods made from the above =====================
    # Not added to production / calorie totals (their calories are already counted
    # in the raw crop they're made from). Listed and quantified separately.
    {"key": "flour", "en": "Flour", "cat": "processed", "kind": "processed",
     "ko": ["밀가루", "가루생산"], "en_terms": ["flour"],
     "yield_t_ha": None, "kcal_per_kg": 3640, "enduse": {"food": 1.0}},
    {"key": "rice_cake", "en": "Rice cake / tteok", "cat": "processed", "kind": "processed",
     "ko": ["떡", "가래떡", "절편"], "en_terms": ["rice cake", "tteok"],
     "yield_t_ha": None, "kcal_per_kg": 2300, "enduse": {"food": 1.0}},
    {"key": "noodles", "en": "Noodles", "cat": "processed", "kind": "processed",
     "ko": ["국수", "랭면", "냉면", "당면"], "en_terms": ["noodle", "noodles"],
     "yield_t_ha": None, "kcal_per_kg": 1380, "enduse": {"food": 1.0}},
    {"key": "bread", "en": "Bread / baked goods", "cat": "processed", "kind": "processed",
     "ko": ["빵공장", "빵생산", "과자", "비스케트"], "en_terms": ["bread", "biscuit", "pastry"],
     "yield_t_ha": None, "kcal_per_kg": 2650, "enduse": {"food": 1.0}},
    {"key": "tofu", "en": "Tofu / bean curd", "cat": "processed", "kind": "processed",
     "ko": ["두부", "언두부"], "en_terms": ["tofu", "bean curd"],
     "yield_t_ha": None, "kcal_per_kg": 760, "enduse": {"food": 1.0}},
    {"key": "soy_sauce", "en": "Soy sauce / paste", "cat": "processed", "kind": "processed",
     "ko": ["간장", "된장", "고추장", "장공장"],
     "en_terms": ["soy sauce", "soybean paste", "soy paste"],
     "yield_t_ha": None, "kcal_per_kg": 600, "enduse": {"food": 1.0}},
    {"key": "kimchi", "en": "Kimchi / pickles", "cat": "processed", "kind": "processed",
     "ko": ["김치", "김장", "절임"], "en_terms": ["kimchi", "pickle"],
     "yield_t_ha": None, "kcal_per_kg": 350, "enduse": {"food": 1.0}},
    {"key": "cooking_oil", "en": "Cooking oil", "cat": "processed", "kind": "processed",
     "ko": ["식용유", "식물성기름", "콩기름", "들기름", "참기름"],
     "en_terms": ["cooking oil", "edible oil", "vegetable oil"],
     "yield_t_ha": None, "kcal_per_kg": 8840, "enduse": {"food": 1.0}},
    {"key": "sugar", "en": "Sugar / confectionery", "cat": "processed", "kind": "processed",
     "ko": ["설탕", "사탕", "당과", "사탕가루", "엿"], "en_terms": ["sugar", "candy", "confectionery"],
     "yield_t_ha": None, "kcal_per_kg": 3870, "enduse": {"food": 1.0}},
    {"key": "beverage", "en": "Beverages / soft drinks", "cat": "processed", "kind": "processed",
     "ko": ["청량음료", "단물", "음료", "과일즙", "사이다"],
     "en_terms": ["soft drink", "beverage", "juice", "soda"],
     "yield_t_ha": None, "kcal_per_kg": 450, "enduse": {"food": 1.0}},
    {"key": "alcohol", "en": "Liquor / beer", "cat": "processed", "kind": "processed",
     "ko": ["맥주", "소주", "청주", "술공장", "약주"], "en_terms": ["liquor", "beer", "soju", "alcohol"],
     "yield_t_ha": None, "kcal_per_kg": 2750, "enduse": {"food": 1.0}},
    {"key": "dairy_product", "en": "Dairy products", "cat": "processed", "kind": "processed",
     "ko": ["치즈", "빠다", "버터", "젖제품", "유제품"], "en_terms": ["cheese", "butter", "yogurt", "dairy product"],
     "yield_t_ha": None, "kcal_per_kg": 3500, "enduse": {"food": 1.0}},
    {"key": "fish_product", "en": "Fish products", "cat": "processed", "kind": "processed",
     "ko": ["젓갈", "건어물", "통조림", "물고기가공"], "en_terms": ["canned fish", "dried fish", "fish product"],
     "yield_t_ha": None, "kcal_per_kg": 1300, "enduse": {"food": 1.0}},
    {"key": "meat_product", "en": "Meat products", "cat": "processed", "kind": "processed",
     "ko": ["소세지", "소시지", "햄", "고기가공"], "en_terms": ["sausage", "ham", "meat product"],
     "yield_t_ha": None, "kcal_per_kg": 3000, "enduse": {"food": 1.0}},
    {"key": "starch", "en": "Starch", "cat": "processed", "kind": "processed",
     "ko": ["농마", "녹말", "전분"], "en_terms": ["starch"],
     "yield_t_ha": None, "kcal_per_kg": 3810, "enduse": {"food": 1.0}},

    # Rice-specific processed sub-products (kept distinct so rice turned into them
    # is not counted as directly-eaten rice).
    {"key": "rice_flour", "en": "Rice flour", "cat": "processed", "kind": "processed",
     "ko": ["쌀가루"], "en_terms": ["rice flour"],
     "yield_t_ha": None, "kcal_per_kg": 3660, "enduse": {"food": 1.0}},
    {"key": "rice_cake", "en": "Rice cake (tteok)", "cat": "processed", "kind": "processed",
     "ko": ["떡", "가래떡"], "en_terms": ["rice cake"],
     "yield_t_ha": None, "kcal_per_kg": 2300, "enduse": {"food": 1.0}},
    {"key": "rice_wine", "en": "Rice wine / liquor", "cat": "processed", "kind": "processed",
     "ko": ["청주", "농태기", "막걸리"], "en_terms": ["rice wine", "makgeolli"],
     "yield_t_ha": None, "kcal_per_kg": 1340, "enduse": {"food": 1.0}},
    {"key": "confection", "en": "Confectionery / dessert", "cat": "processed", "kind": "processed",
     "ko": ["당과류", "과자류"], "en_terms": ["confectionery", "dessert"],
     "yield_t_ha": None, "kcal_per_kg": 4000, "enduse": {"food": 1.0}},
]

COMMODITY_BY_KEY = {c["key"]: c for c in COMMODITIES}

# ---------------------------------------------------------------------------
# Conversion model used by agri_model to route production through end-uses.
# All figures are conservative editable estimates.
# ---------------------------------------------------------------------------
SPOILAGE_DEFAULT = 0.12          # post-harvest loss when not otherwise stated

# End-use label -> (type, target). type ∈ food|seed|loss|feed|process|export|other.
# For feed, target is the livestock commodity the feed is converted into; for
# process, target is the processed-commodity it becomes (None = generic, no route).
USE_LABELS = {
    "food": ("food", None), "direct": ("food", None), "direct consumption": ("food", None),
    "seed": ("seed", None), "seed/other": ("seed", None),
    "spoilage": ("loss", None), "loss": ("loss", None), "post-harvest loss": ("loss", None),
    "fodder": ("feed", "pork"), "feed": ("feed", "pork"), "animal feed": ("feed", "pork"),
    "fodder/other": ("feed", "pork"),
    "rice flour": ("process", "rice_flour"), "rice cake": ("process", "rice_cake"),
    "rice wine": ("process", "rice_wine"), "wine": ("process", "rice_wine"),
    "juice/wine": ("process", "beverage"), "juice": ("process", "beverage"),
    "flour": ("process", "flour"), "oil": ("process", "cooking_oil"),
    "kimchi/processing": ("process", "kimchi"), "jam/processed": ("process", "confection"),
    "dried/processed": ("process", "confection"), "confection": ("process", "confection"),
    "processing": ("process", None), "processed": ("process", None),
    "processing/industry": ("other", None), "export": ("export", None),
    "export/processing": ("export", None), "other": ("other", None),
}

# Processing mass yield: tonnes of product per tonne of raw commodity routed in.
PROCESS_YIELD = {
    "rice_flour": 0.90, "flour": 0.78, "rice_cake": 0.95, "rice_wine": 0.45,
    "beverage": 0.60, "cooking_oil": 0.20, "kimchi": 1.10, "confection": 0.70,
    "sugar": 0.12, "tofu": 2.20, "noodles": 0.90, "starch": 0.80,
}
PROCESS_YIELD_DEFAULT = 0.70

# Feed conversion: kg of feed per kg of edible product, by livestock commodity.
# tonnes_product = feed_tonnes / FCR. (Already expressed per *edible* kg.)
FEED_FCR = {"pork": 6.0, "chicken": 4.2, "beef": 16.0, "goat": 11.0,
            "egg": 4.0, "milk": 1.4, "fish": 2.0, "duck": 4.5, "rabbit": 5.0}
FEED_FCR_DEFAULT = 6.0
DEFAULT_FEED_TARGET = "pork"


def use_type(label: str):
    """(type, target_commodity) for an end-use label; unknown -> ('other', None)."""
    return USE_LABELS.get((label or "").strip().lower(), ("other", None))


def enduse_entries(key: str) -> list[dict]:
    """The built-in default end-use split for a commodity, as typed entries
    [{label, frac, type, target}]. Used as the fallback when no AI/DB-discovered
    breakdown exists for the commodity."""
    c = COMMODITY_BY_KEY.get(key)
    if not c:
        return []
    out = []
    for label, frac in c.get("enduse", {}).items():
        t, target = use_type(label)
        out.append({"label": label, "frac": frac, "type": t, "target": target})
    return out
