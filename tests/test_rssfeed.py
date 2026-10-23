"""Tests for app.rssfeed - the 38 North / Daily NK style RSS ingestion layer.

Fixtures are synthetic (no live network, no publisher content checked in) but are
shaped like the real WordPress feeds, including the traps this module exists for:
full-page content:encoded, Hangul asset paths, RFC 2822 dates, missing images.
"""
from datetime import date, datetime, timezone

from app import rssfeed

BODY = (
    "<div class='entry-content'>"
    "<script>var tracking = 1;</script>"
    "<style>.ad{display:none}</style>"
    "<p>First paragraph with &rsquo;quotes&rsquo; and&nbsp;nbsp.</p>"
    "<figure><img src='/wp-content/uploads/2026/10/Fig-1-1024x600.jpg' />"
    "<figcaption>Satellite image: something</figcaption></figure>"
    "<p>Second paragraph.</p>"
    "<iframe src='https://twitter.com/x/embed/1'></iframe>"
    "<ul><li>A bullet point</li><li>Another one</li></ul>"
    "<!-- comment -->"
    "<p class='wp-block-paragraph'>The post Foo appeared first on 38 North.</p>"
    "</div>"
)

FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
 <channel>
  <title>Sample</title><link>https://example.org/</link><description>d</description>
  <item>
   <title>Item &amp; One</title>
   <link>https://example.org/2026/10/item-one?utm_source=rss#frag</link>
   <pubDate>Thu, 22 Oct 2026 14:19:09 +0000</pubDate>
   <dc:creator>Jane Reporter</dc:creator>
   <category><![CDATA[Satellite Imagery]]></category>
   <category><![CDATA[Domestic Affairs]]></category>
   <category><![CDATA[90s Tag]]></category>
   <description><![CDATA[<p>A short summary.</p>]]></description>
   <content:encoded><![CDATA[{BODY}]]></content:encoded>
  </item>
  <item>
   <title>No body, excerpt only</title>
   <link>https://example.org/2026/10/two</link>
   <pubDate>Tue, 20 Oct 2026 19:05:34 +0000</pubDate>
   <description>Plain summary text.</description>
   <enclosure url="https://example.org/uploads/pic.jpg" type="image/jpeg" length="1"/>
  </item>
  <item>
   <title>Hangul asset</title>
   <link>https://example.org/2026/10/three</link>
   <content:encoded><![CDATA[<p>body</p>
    <img src="https://example.org/uploads/sites/2/2018/06/단속-696x419.jpg" />
    <img src="https://example.org/wp-includes/images/smiley.png" />]]></content:encoded>
  </item>
  <item><title>Linkless</title></item>
 </channel>
</rss>
""".encode("utf-8")


def test_parse_feed_returns_items_in_order():
    items = rssfeed.parse_feed(FEED)
    assert [i.title for i in items] == [
        "Item & One", "No body, excerpt only", "Hangul asset"]


def test_item_metadata_from_namespaces():
    item = rssfeed.parse_feed(FEED)[0]
    assert item.author == "Jane Reporter"
    assert item.published == date(2026, 10, 22)
    assert item.published_dt.year == 2026 and item.published_dt.tzinfo is not None
    assert item.categories == ["Satellite Imagery", "Domestic Affairs", "90s Tag"]
    assert item.excerpt == "A short summary."


def test_article_id_is_permalink_derived_and_stable():
    first = rssfeed.parse_feed(FEED)[0]
    # Query string and fragment are stripped, so a re-run that sees the
    # ?utm_source=rss link maps to the same row instead of duplicating.
    assert first.id == rssfeed.article_id("https://example.org/2026/10/item-one")
    assert first.id == rssfeed.article_id("HTTPS://Example.ORG/2026/10/item-one#x")
    assert len(first.id) == 32
    assert first.id != rssfeed.article_id("https://example.org/2026/10/other")


def test_body_text_strips_chrome_and_boilerplate():
    text = rssfeed.parse_feed(FEED)[0].body_text
    assert "First paragraph with \u2019quotes\u2019 and nbsp." in text   # U+2019 kept
    assert "Second paragraph." in text
    assert "A bullet point" in text
    assert "tracking" not in text and "display:none" not in text
    assert "appeared first on" not in text            # WordPress footer line
    assert "Satellite image: something" not in text   # figcaption dropped
    assert "\n\n" in text                             # paragraph contract
    assert "<" not in text                            # no markup leaks through


def test_figure_images_are_article_images():
    """The trap this exists for: WordPress wraps every article photo in a
    <figure>, so the image pass must see inside figures even though the text pass
    drops them (and their captions)."""
    item = rssfeed.parse_feed(FEED)[0]
    assert item.image_urls == [
        "https://example.org/wp-content/uploads/2026/10/Fig-1-1024x600.jpg"]
    assert "Satellite image" not in item.body_text      # caption still excluded


def test_lazy_loaded_data_src_is_picked_up():
    urls = rssfeed.images_from_html(
        "<figure><img data-src='https://cdn.example/x/Real-768x432.jpg' "
        "src='data:image/gif;base64,AAA'></figure>", "")
    assert urls == ["https://cdn.example/x/Real-768x432.jpg"]


def test_images_from_html_extracts_and_filters():
    urls = rssfeed.images_from_html(
        "<p><img src='/uploads/a.jpg'>"
        "<img src='https://cdn.example/x/logo-2.png'>"
        "<img src='https://cdn.example/x/Photo.JPG'>"
        "<img src='data:image/gif;base64,AAA'>"
        "<img src='/avatar/me.png'></p>", "https://example.org/post/1")
    assert urls == ["https://example.org/uploads/a.jpg",
                    "https://cdn.example/x/Photo.JPG"]


def test_hangul_asset_is_encoded_and_smiley_dropped():
    item = rssfeed.parse_feed(FEED)[2]
    assert len(item.image_urls) == 1
    url = item.image_urls[0]
    assert "%" in url and "단속" not in url
    url.encode("ascii")                    # requestable without an IRI encoder


def test_encode_iri_is_idempotent():
    once = rssfeed.encode_iri("https://e.org/a%20b/단속.jpg")
    assert once == rssfeed.encode_iri(once)
    assert rssfeed.encode_iri("/relative/단속.jpg") == "/relative/단속.jpg"


def test_missing_content_falls_back_to_description_and_enclosure():
    item = rssfeed.parse_feed(FEED)[1]
    assert item.body_text == "Plain summary text."
    assert item.image_urls == ["https://example.org/uploads/pic.jpg"]
    assert item.published == date(2026, 10, 20)


def test_parse_datetime_variants():
    assert rssfeed.parse_datetime("Thu, 22 Oct 2026 14:19:09 +0000").hour == 14
    iso = rssfeed.parse_datetime("2026-10-22T16:00:00+02:00")
    assert iso == datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
    assert rssfeed.parse_datetime("not a date") is None
    assert rssfeed.parse_datetime("") is None


def test_category_key_mapping_slugs_and_bounds():
    assert rssfeed.category_key("Satellite Imagery") == ("satellite", "Satellite Imagery")
    assert rssfeed.category_key("Analysis & Opinion") == ("analysis", "Analysis & Opinion")
    key, label = rssfeed.category_key("  China-North Korea Border  ")
    assert key and len(key) <= 32 and label.startswith("China")
    assert rssfeed.category_key("90s Tag")[0].startswith("c")   # digit-safe key
    assert rssfeed.category_key("   ") == ("", "")
    assert rssfeed.category_key("&lt;tag&gt;") == ("tag", "<tag>")


def test_category_key_never_exceeds_column_width():
    key, label = rssfeed.category_key("A" * 200)
    assert len(key) <= 32 and len(label) <= 64


def test_category_pairs_dedupe_by_key():
    pairs = rssfeed.category_pairs(["News", "news", "Analysis & Opinion", ""])
    assert [k for k, _ in pairs] == ["news", "analysis"]


def test_empty_feed_and_atom_feed():
    assert rssfeed.parse_feed(b"") == []
    atom = (b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
            b"<entry><title>A</title>"
            b"<link rel='self' href='https://e.org/self'/>"
            b"<link rel='alternate' href='https://e.org/post'/>"
            b"<updated>2026-10-21T10:00:00Z</updated>"
            b"<content type='html'>&lt;p&gt;Hello&lt;/p&gt;</content></entry></feed>")
    items = rssfeed.parse_feed(atom)
    assert len(items) == 1 and items[0].link == "https://e.org/post"
    assert items[0].published == date(2026, 10, 21)


def test_tag_labels_drops_byline_dupe_and_caps():
    labels = ["Satellite Imagery", "Domestic Affairs", "Martyn Williams",
              "martyn williams", "border fence", "domestic affairs", "sinuiju"]
    kept = rssfeed.tag_labels(labels, author="Martyn Williams", max_count=4)
    assert kept == ["Satellite Imagery", "Domestic Affairs", "border fence",
                    "sinuiju"]


def test_tag_labels_no_author_and_no_cap():
    labels = ["News", "news", "  ", "Trade"]
    assert rssfeed.tag_labels(labels) == ["News", "Trade"]
    many = [f"tag{i}" for i in range(30)]
    assert len(rssfeed.tag_labels(many, max_count=0)) == 30
    assert rssfeed.tag_labels([]) == []
    # A single-word author must not swallow every tag that mentions the word.
    assert rssfeed.tag_labels(["China trade"], author="China") == ["China trade"]


def test_strip_chrome_leaves_prose():
    cleaned = rssfeed.strip_chrome(BODY)
    assert "First paragraph" in cleaned
    for token in ("script", "figcaption", "iframe", "comment"):
        assert token not in cleaned


def test_best_text_prefers_body_then_excerpt():
    items = rssfeed.parse_feed(FEED)
    assert items[0].best_text.startswith("First paragraph")
    assert items[1].best_text == "Plain summary text."


def test_malformed_xml_raises_for_the_caller_to_log():
    import pytest

    with pytest.raises(Exception):
        rssfeed.parse_feed(b"<rss><channel><item>")

