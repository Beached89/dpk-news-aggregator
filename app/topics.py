"""Content-based topic classification.

The "Juche" category tags articles about self-reliance — farming, food
production, foodstuffs, and domestic light-industry manufacturing (tractors,
clothing, consumer goods) — while EXCLUDING military, missiles/nuclear,
politics, and foreign affairs.

It's a transparent keyword classifier (no model): an article is Juche when it
contains an INCLUDE term and NO EXCLUDE term. Tune the lists below freely.
Keywords are matched as substrings against the Korean title + body, so they are
deliberately multi-character to avoid false hits (e.g. bare "군" also means
"county", bare "총" appears in "총비서").
"""
from __future__ import annotations

# --- self-reliance / production topics --------------------------------------
INCLUDE_JUCHE = [
    # agriculture & farming
    "농업", "농사", "농장", "협동농장", "농촌", "농기계", "알곡", "곡물", "벼농사",
    "강냉이", "옥수수", "감자", "밀农", "수확", "비료", "거름", "관개", "물길",
    "종자", "모내기", "가을걷이", "포전", "정보당",
    # livestock / fisheries / produce
    "축산", "축산물", "양어", "양식장", "양어장", "양계", "닭공장", "돼지공장",
    "젖소", "버섯", "남새", "채소", "온실", "과수", "果樹", "수산", "어업", "어로",
    "물고기", "양묘", "사료",
    # food production / foodstuffs
    "식량", "식료품", "식료공장", "식품", "가공", "된장", "간장", "소금", "제염",
    "빵공장", "사탕", "당과",
    # light industry / domestic manufacturing / consumer goods
    "경공업", "방직", "방적", "직물", "피복", "옷", "신발", "신발공장", "비날론",
    "일용품", "일용공업", "인민소비품", "생활필수품", "가구", "공업품",
    "뜨락또르", "트랙터", "기계공장", "설비", "부속품",
    # self-reliance language
    "국산화", "자력갱생", "자급자족", "증산", "원료기지", "현대화",
]

# --- excluded topics (override INCLUDE) -------------------------------------
EXCLUDE_JUCHE = [
    # military
    "군사", "군대", "인민군", "군인", "국방", "무기", "무장", "전투", "사격",
    "병기", "군수", "잠수함", "함대", "포탄", "방사포", "대포", "전략무기", "훈련",
    # missiles / nuclear
    "미싸일", "미사일", "로케트", "탄도", "핵무기", "핵시험", "핵계획", "발사",
    # politics
    "전원회의", "정치국", "당대회", "최고인민회의", "선거", "대의원", "헌법",
    "정권", "대회보고",
    # foreign affairs
    "외무성", "외무상", "외교", "대외", "친선", "정상회담", "수뇌상봉", "대사관",
    "대사", "제재", "담화", "성명", "회담", "공동보도", "축전",
]


def classify_juche(text: str) -> bool:
    """True if the text is about self-reliance (and not an excluded topic)."""
    if not text:
        return False
    if any(term in text for term in EXCLUDE_JUCHE):
        return False
    return any(term in text for term in INCLUDE_JUCHE)


# --- broader scraping filter ------------------------------------------------
# What to actually KEEP from KCNA/Rodong now: photo articles about self-reliance,
# food, agriculture, foodstuffs, manufacturing, textiles, heavy & light industry,
# and the policies affecting them. = the Juche set + heavy industry + general
# manufacturing terms. Policy language is allowed (NOT excluded), so a Party
# decision about agriculture or industry still qualifies.
INCLUDE_TOPICS = INCLUDE_JUCHE + [
    # heavy industry
    "중공업", "금속공업", "금속", "제철", "제강", "강철", "압연", "선철", "주물", "단조",
    "광업", "광산", "채굴", "채광", "석탄", "무연탄", "탄광", "갱",
    "화학공업", "화학", "비료공장", "시멘트", "세멘트",
    "기계공업", "공작기계", "조선소", "발전소", "전력", "수력발전", "화력발전", "변전소",
    # general manufacturing / production
    "공업", "생산", "제조", "제작", "생산공정", "생산라인", "증산운동", "기술혁신",
    # textiles (beyond the light-industry set above)
    "섬유", "방직공장", "직조", "견직", "잠업", "누에", "양말", "의류", "메리야스",
    # food / foodstuffs (extra)
    "농축산물", "수산물", "청량음료", "식용유", "기초식품",
    # policy language tied to the economy
    "경제정책", "경제계획", "인민경제", "국가경제발전", "생산계획", "증산투쟁",
]

# Only weapons/military content is hard-blocked (it can share words like "기계"
# or "생산"); everything else is decided purely by the INCLUDE list.
EXCLUDE_TOPICS = [
    "군사", "인민군", "국방", "무기", "무장", "전투", "사격", "병기", "군수",
    "잠수함", "함대", "포탄", "방사포", "전략무기",
    "미싸일", "미사일", "로케트", "탄도", "핵무기", "핵시험", "핵계획",
]


# --- agriculture / foodstuffs (broad; photos NOT required) ------------------
# IMPORTANT: every term must be unambiguous as a *substring*. Bare single roots
# like "밀" (wheat) or "벼" (rice plant) are forbidden here because they also
# occur inside unrelated words — 비밀(secret), 긴밀(close), 정밀(precision),
# 벼락(lightning) — and would misclassify military/political articles as farming.
# Use the compound forms (밀농사, 밀가루, 벼농사…) instead.
AGRI_KEYWORDS = [
    # farming & crops
    "농업", "농사", "농장", "협동농장", "농촌", "농기계", "농기구", "알곡", "곡물",
    "벼농사", "볍씨", "논벼", "강냉이", "옥수수", "감자", "고구마",
    "밀농사", "밀가루", "밀밭", "보리", "콩농사", "대두", "수확", "가을걷이",
    "모내기", "비료", "거름", "퇴비", "관개", "물길", "종자", "포전", "정보당",
    "다수확", "영농", "온실", "남새", "채소", "과수", "과수원", "과일", "버섯",
    "약초", "담배농사",
    # livestock & fisheries
    "축산", "축산물", "양어", "양식장", "양어장", "양계", "닭공장", "돼지공장",
    "젖소", "양묘", "먹이풀", "수산물", "수산업", "어업", "어로작업", "물고기", "양식어",
    # food / foodstuffs / processing
    "식량", "식료품", "식료공장", "식품", "먹거리", "영양가", "식품가공", "농산물가공",
    "된장", "간장", "제염", "빵공장", "과자", "사탕", "당과", "기초식품", "청량음료",
    "식용유", "농축산물",
    # machinery / inputs tied to farming
    "뜨락또르", "트랙터", "이앙기", "종합수확기", "탈곡기",
]

# Hard-block weapons / military so a defence article that happens to mention a
# food-supply word isn't pulled into the farming archive. (Politics is NOT
# excluded — a Party decision about agriculture still belongs here.)
EXCLUDE_AGRI = EXCLUDE_TOPICS

# A commodity word alone isn't enough — countless articles (about homes, mines,
# machines, heritage, tourism) mention a food in passing. To keep only items that
# can plausibly be reasoned into PRODUCTION VOLUMES, also require a production /
# harvest / quantity signal. This is what filters out the "useless" pulls.
AGRI_PRODUCTION_SIGNALS = [
    "생산", "수확", "재배", "파종", "모내기", "이앙", "증산", "다수확", "작황", "소출",
    "수매", "출하", "양식", "어획", "어로", "수확고", "어획고", "결실", "포전", "비배관리",
    "알곡", "낟알", "풍년", "수확량", "생산량", "작물", "그루바꿈", "조성",
    "정보", "헥타르", "핵타르", "톤", "마리",
]
AGRI_PRODUCTION_SIGNALS_EN = [
    "harvest", "yield", "cultivat", "planted", "planting", "sowing", "hectare",
    "tonne", "tons of", "crop", "production", "output", "fishing", "breeding", "farming",
]


def is_agriculture(text: str) -> bool:
    """True only if the text is about food/farming PRODUCTION that could be
    quantified — i.e. it names a commodity AND carries a production/harvest/
    quantity signal. A bare passing mention of a food word is NOT enough, which
    keeps anecdotes, construction, mining, tourism and heritage pieces out."""
    if not text:
        return False
    if any(term in text for term in EXCLUDE_AGRI):
        return False
    if not any(term in text for term in AGRI_KEYWORDS):
        return False
    if any(s in text for s in AGRI_PRODUCTION_SIGNALS):
        return True
    low = text.lower()
    return any(s in low for s in AGRI_PRODUCTION_SIGNALS_EN)


def is_on_topic(text: str) -> bool:
    """True if the article is about one of the kept economic/industrial topics
    (and not weapons/military). Used to filter what the scrapers publish."""
    if not text:
        return False
    if any(term in text for term in EXCLUDE_TOPICS):
        return False
    return any(term in text for term in INCLUDE_TOPICS)
