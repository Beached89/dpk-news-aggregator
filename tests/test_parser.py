"""Parser tests against fixtures shaped like real KCNA pages.

The structure mirrors what was re-validated live on 2026-10-23, after KCNA
rebuilt the site: `/{lang}/article/detail/<32 hex>` pages with the headline in
`<h1>` and paragraphs as bare `<p>` inside `<article>`, `[2026.10.20.]` date
stamps on listings, photos on a separate `gallery/detail` page served from
`/photo/<hex>`, and video streams at `/{lang}/video/stream/<hex>`.
"""
import datetime as dt

from app.parser import (ARTICLE_RE, LEGACY_ARTICLE_RE, article_path,
                        extract_article_links, extract_categories,
                        extract_gallery_urls, extract_listing,
                        extract_photo_urls, parse_article)

HASH = "a1aa4999d4d6e3129717a015272723eb"
GALLERY = "8a68a6ce07bc4c693e6e653b6face954"
PHOTO_A = "0e055e1f78d2513958769c999314b2da" + "9e0f99f2027e90e5c5033d53cce6ab2b"
PHOTO_B = "0e055e1f78d2513958769c999314b2da" + "115a1888262f42830c7c822bed001ce4"

ARTICLE_HTML = f"""
<!doctype html><html><body lang="ko">
 <header><a class="logo" href="/kp"><img src="/images/logo.png" alt="조선중앙통신"></a></header>
 <main>
  <article>
   <div class="container">
    <h1 class="text-center text-danger"><nobr>경애하는</nobr> <nobr><august_name>김정은</august_name>동지께서</nobr> 공업시설을 돌아보시였다</h1>
    <h1 class="text-center text-danger"></h1>
    <p>(평양 10월 20일발 조선중앙통신)</p>
    <p>현지에서 책임일군들이 맞이하였다.</p>
    <p>생산정상화를 강조하시였다. 로동자들이 분발하였다.</p>
    <a class="right_button gallery_button" href="/kp/gallery/detail/{GALLERY}" target="_blank">
      <i class="fa fa-camera"></i>
    </a>
    <a class="right_button increase_button"><i class="fa fa-search-plus"></i></a>
   </div>
  </article>
 </main>
</body></html>
"""

ARTICLE_WITH_SUBTITLE = f"""
<main><article><div class="container">
  <h1>제목</h1><h1>부제목</h1>
  <p>(평양 12월 31일발 조선중앙통신)</p><p>본문 한 문단.</p>
</div></article></main>
"""

EN_ARTICLE_HTML = """
<main><article><div class="container">
  <h1>Respected Comrade Goes round Industrial Facility</h1><h1></h1>
  <p>Pyongyang, October 20 (KCNA) -- Respected Comrade went round the facility.</p>
  <p>He was accompanied by leading officials.</p>
</div></article></main>
"""

HOME_HTML = f"""
<html><body>
 <nav class="language"><a href="/en" lang="en-us">English</a></nav>
 <aside>
  <a href="/kp/article/list/6a47505ba5268fd7749c0fe11e4b24b4">중요소식</a>
  <a href="/kp/article/list/1afa96195f9b303902490a126ab7285f">문건</a>
  <a href="/kp/gallery/list/6837a75abf5c6249d0e39ee758e763ea">사진목록</a>
 </aside>
 <div class="article">
  <h5 class="text-center">
   <a target="_blank" href="/kp/article/detail/{HASH}"><nobr>경애하는</nobr> 기사 하나</a>
   <span class="publish_date"><nobr>[2026.10.20.]</nobr></span>
  </h5>
 </div>
 <div class="article">
  <h5 class="block">
   <a href="/kp/article/detail/73eeeeab2f63e82feae5694a75c22f46">기사 둘</a>
   <span><nobr>[2026.10.22.]</nobr></span>
  </h5>
 </div>
 <div class="article">
  <h5 class="block"><a href="/kp/article/detail/6695f38bbba7e82feae5694a75c2aaaa">날짜 없는 기사</a></h5>
 </div>
 <a href="/en/article/detail/{HASH}">English twin</a>
</body></html>
"""

LISTING_HTML = f"""
<main><div class="row">
 <div class="article"><h5 class="block">
   <a href="/kp/article/detail/{HASH}" data-rel="fancybox-button" target="_blank">
     <i class="fa fa-angle-right"></i> 기사 하나</a>
   <span><nobr>[2026.10.22.]</nobr></span></h5></div>
 <div class="article"><h5 class="block">
   <a href="/kp/article/detail/25a23d8b1cf8303902490a126ab7285f">기사 둘</a>
   <span><nobr>[2026.10.21.]</nobr></span></h5></div>
</div></main>
"""

GALLERY_HTML = f"""
<main><div class="gallery"><div class="owl-wrapper">
  <div class="item"><div class="thumbnail-img">
    <img class="img-responsive" src="/photo/{PHOTO_A}"  alt="기사 제목"></div></div>
  <div class="item"><div class="thumbnail-img">
    <img class="img-responsive" src="/photo/{PHOTO_B}"  alt="기사 제목"></div></div>
</div></div></main>
"""

VIDEO_HTML = """
<main><div class="video"><video controls="controls" autoplay>
  <source src="/kp/video/stream/c407bd4352cb6d5385636b7861fe2d7a1c6e413be836696e16bd2bab7d234b8e" type="video/mp4">
  <source src="/kp/video/stream/c407bd4352cb6d5385636b7861fe2d7a836a02f90af6d36096f9ea2acb7247fd" type="video/ogg">
</video></div></main>
"""

# ---------------------------------------------------------------- URL shapes

def test_article_path_builds_new_scheme():
    assert article_path("kp", HASH) == f"/kp/article/detail/{HASH}"


def test_article_re_accepts_new_and_rejects_listing():
    assert ARTICLE_RE.search(f"/kp/article/detail/{HASH}")
    assert not ARTICLE_RE.search("/kp/article/list/6a47505ba5268fd7749c0fe11e4b24b4")


def test_legacy_article_re_still_resolves_old_links():
    old = "/kp/article/q/3473ad07d013a0da59a0e4178eaa7955.kcmsf"
    assert not ARTICLE_RE.search(old)
    assert LEGACY_ARTICLE_RE.search(old).group("hash") == "3473ad07d013a0da59a0e4178eaa7955"


def test_extract_article_links_dedupes_by_hash():
    links = extract_article_links(HOME_HTML)
    assert {h for _, h in links} == {
        HASH, "73eeeeab2f63e82feae5694a75c22f46", "6695f38bbba7e82feae5694a75c2aaaa"}
    # the /en twin shares the Korean hash, so it adds no extra entry
    assert len(links) == 3
    assert {lg for lg, _ in links} == {"kp", "en"}


# ---------------------------------------------------------------- listings

def test_extract_categories_returns_article_listing_links():
    cats = extract_categories(HOME_HTML)
    assert [label for _, label in cats] == ["중요소식", "문건"]
    assert all("/article/list/" in path for path, _ in cats)


def test_extract_listing_reads_the_stamp_next_to_each_link():
    items = {i.hash: i for i in extract_listing(HOME_HTML)}
    assert items[HASH].published_date == dt.date(2026, 10, 20)
    assert items["73eeeeab2f63e82feae5694a75c22f46"].published_date == dt.date(2026, 10, 22)
    assert items["6695f38bbba7e82feae5694a75c2aaaa"].published_date is None
    assert "기사 하나" in items[HASH].title


def test_extract_listing_on_a_category_page():
    items = extract_listing(LISTING_HTML)
    assert [i.published_date for i in items] == [dt.date(2026, 10, 22), dt.date(2026, 10, 21)]


# ---------------------------------------------------------------- article pages

def test_parse_article_fields():
    a = parse_article(ARTICLE_HTML, "kp", HASH)
    # <nobr>/<august_name> markup inside the headline must not break extraction
    assert a.title == "경애하는 김정은동지께서 공업시설을 돌아보시였다"
    assert a.subtitle is None                     # the template's second <h1> is empty
    assert a.dateline.startswith("(평양")
    assert a.body_text.count("\n\n") >= 2
    assert "생산정상화를" in a.body_text
    assert a.gallery_url == f"/kp/gallery/detail/{GALLERY}"
    assert a.image_urls == []                     # photos are no longer inline
    assert a.video_urls == []


def test_parse_article_subtitle_when_present():
    a = parse_article(ARTICLE_WITH_SUBTITLE, "kp", HASH)
    assert (a.title, a.subtitle) == ("제목", "부제목")


def test_parse_article_logo_is_never_a_photo():
    a = parse_article(ARTICLE_HTML, "kp", HASH)
    assert all("logo" not in u for u in a.image_urls)


# ---------------------------------------------------------------- dates

def test_date_prefers_the_listing_stamp():
    a = parse_article(ARTICLE_HTML, "kp", HASH,
                      listing_date=dt.date(2026, 10, 20), year_hint=1999)
    assert a.published_date == dt.date(2026, 10, 20)


def test_date_falls_back_to_dateline_plus_year_hint():
    a = parse_article(ARTICLE_HTML, "kp", HASH, year_hint=2026)
    assert a.published_date == dt.date(2026, 10, 20)


def test_date_rollover_backs_the_year_off():
    from app.parser import _safe_date
    assert _safe_date(2026, 12, 31, today=dt.date(2026, 1, 15)) == dt.date(2025, 12, 31)
    assert _safe_date(2026, 1, 15, today=dt.date(2026, 1, 20)) == dt.date(2026, 1, 15)


def test_parse_article_english_dateline():
    a = parse_article(EN_ARTICLE_HTML, "en", HASH, year_hint=2026)
    assert a.title.startswith("Respected Comrade")
    assert a.dateline == "Pyongyang, October 20 (KCNA)"
    assert a.published_date == dt.date(2026, 10, 20)


# ---------------------------------------------------------------- media

def test_extract_photo_urls_from_a_gallery_page():
    assert extract_photo_urls(GALLERY_HTML) == [f"/photo/{PHOTO_A}", f"/photo/{PHOTO_B}"]


def test_extract_gallery_urls_from_an_article_page():
    assert extract_gallery_urls(ARTICLE_HTML) == [f"/kp/gallery/detail/{GALLERY}"]


def test_parse_article_picks_up_video_streams():
    a = parse_article(VIDEO_HTML, "kp", HASH)
    assert len(a.video_urls) == 2
    assert all("/kp/video/stream/" in u for u in a.video_urls)


# ---------------------------------------------------------------- degenerate input

def test_missing_fields_graceful():
    a = parse_article("<html><body><p>아무것도 없음</p></body></html>", "kp", "x")
    assert a.title is None
    assert a.image_urls == []
    assert a.gallery_url is None
    assert a.published_date is None


def test_empty_document():
    a = parse_article("", "kp", "x")
    assert a.title is None and a.body_text is None

