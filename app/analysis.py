"""Korean morphological analysis for learning mode (Phase 4).

Uses Kiwi (kiwipiepy) — offline, no models to download at runtime, no API keys.
Produces, per article: paragraphs aligned KO↔EN (by index, since KCNA
translations run parallel), each Korean paragraph broken into sentences and
whitespace words, each word carrying its morpheme breakdown with plain-English
POS labels and a curated grammar glossary for particles/endings.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Optional

# ---- POS tag -> plain-English label (Kiwi/Sejong tagset) ------------------
POS_LABEL = {
    "NNG": "noun", "NNP": "proper noun", "NNB": "bound noun", "NP": "pronoun",
    "NR": "number", "VV": "verb", "VA": "adjective", "VX": "auxiliary verb",
    "VCP": "copula (is)", "VCN": "negative copula", "MM": "determiner",
    "MAG": "adverb", "MAJ": "conjunctive adverb", "IC": "interjection",
    "JKS": "subject particle", "JKC": "complement particle",
    "JKG": "possessive particle", "JKO": "object particle",
    "JKB": "adverbial particle", "JKV": "vocative particle",
    "JKQ": "quotative particle", "JX": "auxiliary particle", "JC": "connective particle",
    "EP": "pre-final ending", "EF": "sentence ending", "EC": "connective ending",
    "ETN": "nominalizing ending", "ETM": "adnominal ending",
    "XPN": "prefix", "XSN": "noun suffix", "XSV": "verb-forming suffix",
    "XSA": "adjective-forming suffix", "XR": "root",
    "SF": "punctuation", "SP": "punctuation", "SS": "punctuation",
    "SE": "punctuation", "SO": "punctuation", "SW": "symbol",
    "SL": "foreign word", "SH": "hanja", "SN": "number",
}

# Content classes get highlighted; everything else is "functional".
CONTENT_TAGS = {"NNG", "NNP", "NNB", "NP", "NR", "VV", "VA", "VX", "MM", "MAG", "MAJ", "IC", "SL", "SH"}

# ---- curated grammar glossary for common particles/endings (by surface) ----
GRAMMAR_GLOSS = {
    "은": "topic marker (as for)", "는": "topic marker (as for)",
    "이": "subject marker", "가": "subject marker",
    "께서": "honorific subject marker",
    "을": "object marker", "를": "object marker",
    "의": "possessive (of / 's)",
    "에": "at / to (place·time)", "에서": "at / from (place)",
    "에게": "to (someone)", "한테": "to (someone)", "께": "to (honorific)",
    "로": "by / with / toward", "으로": "by / with / toward",
    "와": "and / with", "과": "and / with", "하고": "and / with", "랑": "and / with", "이랑": "and / with",
    "도": "also / too", "만": "only", "까지": "until / up to", "부터": "from",
    "보다": "than (comparison)", "처럼": "like", "같이": "like", "마다": "every",
    "다": "declarative ending", "요": "polite ending",
    "습니다": "formal-polite ending", "ㅂ니다": "formal-polite ending",
    "시": "honorific infix", "었": "past tense", "았": "past tense", "였": "past tense",
    "겠": "future / intention", "고": "and / connective", "지만": "but", "어서": "so / and then",
    "면": "if / when", "는데": "background / but",
}

_kiwi = None
_lock = Lock()

# ---- offline KO->EN dictionary (curated; extensible via a local override) --
_DATA_DIR = Path(__file__).parent / "data"


def _load_dict() -> dict:
    d: dict = {}
    for name in ("dict_ko_en.json", "dict_ko_en.local.json"):
        p = _DATA_DIR / name
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                d.update({k: v for k, v in raw.items() if not k.startswith("_")})
            except Exception:
                pass
    return d


DICT = _load_dict()


def _meaning(form: str, tag: str) -> Optional[str]:
    """English meaning for a content morpheme, via lemma-aware lookup."""
    # nouns / adverbs / determiners / pronouns: surface form
    if form in DICT:
        return DICT[form]
    # verbs & adjectives: Kiwi gives the stem, so try the dictionary -다 form
    if tag in ("VV", "VA", "VX", "VCN") and form + "다" in DICT:
        return DICT[form + "다"]
    # Sino-Korean noun roots that also form 하다-verbs (출발 -> 출발하다)
    if tag in ("NNG", "XR") and form + "하다" in DICT:
        return DICT[form + "하다"]
    return None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        with _lock:
            if _kiwi is None:
                from kiwipiepy import Kiwi
                _kiwi = Kiwi()
    return _kiwi


def _gloss(form: str, tag: str) -> Optional[str]:
    is_gram = tag[0] in ("J", "E") or tag.startswith("XS")
    # particles/endings: grammar role (only for grammatical tags, so the verb
    # stem 가 isn't mistaken for the subject particle 가)
    if is_gram and form in GRAMMAR_GLOSS:
        return GRAMMAR_GLOSS[form]
    # content words: English dictionary meaning
    meaning = _meaning(form, tag)
    if meaning:
        return meaning
    # grammatical morphemes without an exact entry: their grammatical role
    if is_gram:
        return POS_LABEL.get(tag)
    return None


def _word_meaning(morphs: list) -> Optional[str]:
    """Whole-word meaning: join the content morphemes (dropping particles,
    endings and symbols) and look the result up — catches compound proper
    nouns like 조선중앙통신 -> KCNA or 로동신문 -> Rodong Sinmun."""
    core = "".join(m["form"] for m in morphs if m["tag"][0] not in ("J", "E", "S"))
    if core and core in DICT:
        return DICT[core]
    return None


def analyze_word(word: str) -> dict:
    """Break a whitespace word into morphemes with labels and glosses."""
    kiwi = _get_kiwi()
    morphs = []
    is_content = False
    for tok in kiwi.tokenize(word):
        label = POS_LABEL.get(tok.tag, tok.tag)
        if tok.tag in CONTENT_TAGS:
            is_content = True
        morphs.append({
            "form": tok.form, "tag": tok.tag, "label": label,
            "gloss": _gloss(tok.form, tok.tag),
        })
    return {"surface": word, "morphemes": morphs, "is_content": is_content,
            "meaning": _word_meaning(morphs)}


# Strictly nouns (common + proper) and verbs. Deliberately EXCLUDES adjectives,
# adverbs (not/then/too), particles, endings, pronouns, numbers, determiners,
# foreign words and other low-value tokens.
VOCAB_TAGS = {"NNG", "NNP", "VV"}


def extract_vocab(texts: list[str]) -> list[dict]:
    """Pull the major Korean words (with an English meaning) from a sequence of
    text lines, in order of first appearance, deduplicated. Verbs/adjectives are
    shown in dictionary (-다) form. Returns [{"ko": str, "en": str}]."""
    kiwi = _get_kiwi()
    seen: set[str] = set()
    out: list[dict] = []
    for t in texts:
        for tok in kiwi.tokenize(t or ""):
            if tok.tag not in VOCAB_TAGS:
                continue
            en = _meaning(tok.form, tok.tag)
            if not en:
                continue
            disp = tok.form + "다" if tok.tag in ("VV", "VA") else tok.form
            if disp in seen:
                continue
            seen.add(disp)
            out.append({"ko": disp, "en": en})
    return out


def major_words(texts: list[str]) -> list[dict]:
    """Major Korean words (content tags) in order of first appearance, deduped,
    WITHOUT requiring a dictionary entry. Verbs/adjectives in -다 form. Returns
    [{"ko": display, "form": lemma, "tag": pos}] for downstream translation."""
    kiwi = _get_kiwi()
    seen: set[str] = set()
    out: list[dict] = []
    for t in texts:
        for tok in kiwi.tokenize(t or ""):
            if tok.tag not in VOCAB_TAGS:
                continue
            disp = tok.form + "다" if tok.tag in ("VV", "VA") else tok.form
            if not disp or disp in seen:
                continue
            seen.add(disp)
            out.append({"ko": disp, "form": tok.form, "tag": tok.tag})
    return out


def dict_meaning(form: str, tag: str) -> Optional[str]:
    """Public wrapper for the offline dictionary lemma lookup."""
    return _meaning(form, tag)


def _split_en_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


# KCNA datelines: KO is a parenthetical "(평양 …일발 조선중앙통신)"; EN is a prefix
# "Pyongyang, June 14 (KCNA) -- ..." glued to the first sentence. Handling them
# explicitly removes the systematic off-by-one between the two languages.
KO_DATELINE_RE = re.compile(r"\([^()]*통신\)")
EN_DATELINE_PREFIX_RE = re.compile(r"^\s*[A-Z][^.()]*\(KCNA\)\s*(?:--|—|-{1,2})?\s*")


def _line(ko_text: str, en: Optional[str]) -> dict:
    return {"ko_words": [analyze_word(w) for w in ko_text.split()] if ko_text else [], "en": en}


@lru_cache(maxsize=256)
def _analyze_cached(ko_text: str, en_text: str | None):
    """Aligned KO/EN line pairs, grouped by paragraph.

    Returns: list of paragraphs; each paragraph is a list of lines
        {"ko_words": [{surface, morphemes, is_content}], "en": str | None}

    Alignment strategy (robust against KCNA's structure):
      1. The dateline is pulled out of both languages and paired on its own.
      2. Remaining paragraphs are paired by index.
      3. Within a paragraph, sentences are paired one-to-one ONLY when the counts
         match; otherwise the whole Korean paragraph is paired with the whole
         English paragraph (coarser, but never misaligned).
    """
    kiwi = _get_kiwi()
    ko_text = ko_text or ""
    en_text = en_text or ""
    paragraphs: list = []

    # 1) dateline
    ko_dl = None
    m = KO_DATELINE_RE.search(ko_text[:120])
    if m:
        ko_dl = m.group(0)
        ko_text = ko_text.replace(ko_dl, "", 1)
    en_dl = None
    if en_text:
        em = EN_DATELINE_PREFIX_RE.match(en_text)
        if em:
            en_dl = em.group(0).strip().rstrip("-—– ").strip()
            en_text = en_text[em.end():]
    if ko_dl or en_dl:
        paragraphs.append([_line(ko_dl or "", en_dl)])

    ko_paras = [p.strip() for p in ko_text.split("\n\n") if p.strip()]
    en_paras = [p.strip() for p in en_text.split("\n\n") if p.strip()] if en_text else []

    # 2) paragraph-indexed pairing
    for i in range(max(len(ko_paras), len(en_paras))):
        kp = ko_paras[i] if i < len(ko_paras) else None
        ep = en_paras[i] if i < len(en_paras) else None
        lines: list = []
        if kp and ep:
            ko_sents = [s.text.strip() for s in kiwi.split_into_sents(kp) if s.text.strip()]
            en_sents = _split_en_sentences(ep)
            if ko_sents and len(ko_sents) == len(en_sents):
                lines = [_line(ks, es) for ks, es in zip(ko_sents, en_sents)]
            else:
                lines = [_line(kp, ep)]            # counts differ -> whole paragraph
        elif kp:
            lines = [_line(s.text.strip(), None)
                     for s in kiwi.split_into_sents(kp) if s.text.strip()] or [_line(kp, None)]
        elif ep:
            lines = [_line("", ep)]
        if lines:
            paragraphs.append(lines)
    return paragraphs


def analyze_article(ko_text: str, en_text: Optional[str]) -> list:
    """Paragraphs of sentence-aligned KO/EN line pairs (see _analyze_cached)."""
    return _analyze_cached(ko_text or "", en_text)


def analyze_aligned(struct: list) -> list:
    """Build the learning view from precomputed word alignment (see align.py /
    scraper). `struct` is paragraphs of {ko, en, align} sentence pairs, where
    align maps Korean token index (str) -> [English token indices].

    Each Korean word carries `align` (the English token indices it maps to) and
    each line carries `en_words` (the English tokens) so the UI can highlight the
    exact corresponding English words on hover.
    """
    paragraphs: list = []
    for para in struct or []:
        lines: list = []
        for sent in para:
            ko = sent.get("ko") or ""
            en = sent.get("en") or ""
            align = sent.get("align") or {}
            ko_words = []
            for idx, surface in enumerate(ko.split()):
                w = analyze_word(surface)
                w["align"] = align.get(str(idx)) or []
                ko_words.append(w)
            lines.append({"ko_words": ko_words, "en": en,
                          "en_words": en.split(), "aligned": True})
        if lines:
            paragraphs.append(lines)
    return paragraphs
