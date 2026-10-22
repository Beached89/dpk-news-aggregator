"""Tests for Phase 3 (English parsing) and Phase 4 (Korean analysis)."""
import datetime as dt

from app.parser import parse_article, _parse_en_date, _extract_dateline
from app.analysis import analyze_word, analyze_article

EN_HTML = """
<html><body class="en">
  <div class="article-main-title">Comrade Xi Jinping Leaves Pyongyang</div>
  <div class="article-sub-title">Kim Jong Un Warmly Sees Him Off</div>
  <div class="article-content-body"><div class="content-wrapper">
    <p>Pyongyang, June 10 (KCNA) -- Comrade Xi Jinping left here in the afternoon.</p>
    <p>He was seen off at the airport.</p>
  </div></div>
</body></html>
"""


def test_english_article_parses_same_selectors():
    a = parse_article(EN_HTML, "en", "abc")
    assert a.title == "Comrade Xi Jinping Leaves Pyongyang"
    assert a.subtitle.startswith("Kim Jong Un")
    assert a.dateline == "Pyongyang, June 10 (KCNA)"
    assert "afternoon" in a.body_text


def test_en_date_needs_year():
    assert _parse_en_date("Pyongyang, June 10 (KCNA)") is None  # no year -> None
    assert _parse_en_date("dated June 10, Juche 115 (2026)") == dt.date(2026, 6, 10)


def test_extract_dateline_korean():
    assert _extract_dateline(["(평양 6월 10일발 조선중앙통신)", "본문"], "kp") \
        == "(평양 6월 10일발 조선중앙통신)"


def test_analyze_word_morphemes_and_gloss():
    w = analyze_word("김정은동지께서")
    forms = [m["form"] for m in w["morphemes"]]
    assert "께서" in forms
    gloss = next(m["gloss"] for m in w["morphemes"] if m["form"] == "께서")
    assert "subject" in gloss
    assert w["is_content"] is True  # contains a noun


def test_analyze_article_aligns_paragraphs():
    ko = "첫 문단입니다.\n\n두번째 문단입니다."
    en = "This is the first paragraph.\n\nThis is the second paragraph."
    paras = analyze_article(ko, en)
    assert len(paras) == 2
    # each paragraph is a list of {ko_words, en} line pairs
    assert paras[0][0]["en"] == "This is the first paragraph."
    assert paras[1][0]["en"] == "This is the second paragraph."
    assert paras[0][0]["ko_words"][0]["surface"] == "첫"


def test_analyze_article_sentence_level_pairing():
    # two sentences in one paragraph -> two interleaved KO/EN line pairs
    ko = "비가 온다. 바람도 분다."
    en = "It rains. The wind also blows."
    paras = analyze_article(ko, en)
    assert len(paras) == 1 and len(paras[0]) == 2
    assert paras[0][0]["en"] == "It rains."
    assert paras[0][1]["en"] == "The wind also blows."


def test_analyze_article_handles_missing_english():
    paras = analyze_article("문단 하나.", None)
    assert paras[0][0]["en"] is None
