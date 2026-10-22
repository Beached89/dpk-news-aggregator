"""Parser tests against fixtures shaped like real KCNA pages.

The fixture structure mirrors what was validated live on 2026-06-13:
title/subtitle/body selectors, dateline, date format, and the
/{lang}/image/q/<hash>.kcmsf media pattern.
"""
import datetime as dt

from app.parser import extract_article_links, parse_article

ARTICLE_HTML = """
<!doctype html><html lang="ko"><body class="kp">
  <div class="header"><img src="/assets/img/home/mark.png"></div>
  <div class="container">
    <div class="article-content-title">
      <div class="article-main-title">습근평동지 평양 출발</div>
      <div class="article-sub-title">경애하는 김정은동지께서 따뜻이 환송하시였다</div>
    </div>
    <div class="article-content-body">
      <div class="content-wrapper">
        <p>(평양 6월 10일발 조선중앙통신)</p>
        <p>중국공산당 중앙위원회 총서기가 6월 9일 평양을 출발하였다.</p>
        <p>김정은동지께서 환송하시였다. 2026.06.10</p>
        <img class="img-0" src="/kp/image/q/050a14957698031f74b7e39b2e17f462aaaa.kcmsf">
        <img class="img-1" data-src="/kp/image/q/050a14957698031f74b7e39b2e17f462bbbb.kcmsf">
      </div>
    </div>
  </div>
</body></html>
"""

HOME_HTML = """
<html><body>
  <a href="/kp/article/q/3473ad07d013a0da59a0e4178eaa7955.kcmsf">기사1</a>
  <a href="/kp/article/q/c45b70701d5003e4f1669dbc520cf356.kcmsf">기사2</a>
  <a href="/kp/article/q/3473ad07d013a0da59a0e4178eaa7955.kcmsf">중복</a>
  <a href="/kp/category/articles/q/54c0ca4ca013a92cc9cf95bd4004c61a.kcmsf">분류</a>
  <a href="/en/article/q/3473ad07d013a0da59a0e4178eaa7955.kcmsf">EN</a>
</body></html>
"""


def test_extract_article_links_dedupes():
    links = extract_article_links(HOME_HTML)
    hashes = {h for _, h in links}
    assert "3473ad07d013a0da59a0e4178eaa7955" in hashes
    assert "c45b70701d5003e4f1669dbc520cf356" in hashes
    # de-duplicated by hash
    assert len(links) == 2


def test_parse_article_fields():
    a = parse_article(ARTICLE_HTML, "kp", "abc123")
    assert a.title == "습근평동지 평양 출발"
    assert a.subtitle.startswith("경애하는")
    assert a.dateline.startswith("(평양")
    assert a.published_date == dt.date(2026, 6, 10)
    assert "6월 9일" in a.body_text
    # paragraphs joined with blank lines
    assert a.body_text.count("\n\n") >= 2


def test_parse_article_media_pattern():
    a = parse_article(ARTICLE_HTML, "kp", "abc123")
    # both src and data-src images picked up; logo excluded
    assert len(a.image_urls) == 2
    assert all("/kp/image/q/" in u for u in a.image_urls)
    assert all("mark.png" not in u for u in a.image_urls)
    assert a.video_urls == []


def test_parse_article_missing_fields_graceful():
    a = parse_article("<html><body><p>아무것도 없음</p></body></html>", "kp", "x")
    assert a.title is None
    assert a.image_urls == []
