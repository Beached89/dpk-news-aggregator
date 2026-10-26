"""Tests for how the export represents *when* a story was published.

The bug this locks down: `published_date` is a bare DATE, and the front end
turned it into `...T00:00:00Z` and counted minutes from there, so every story
published today displayed "N hours ago" growing all afternoon — a feed that
looks frozen. The export therefore now carries an explicit instant (`at`) plus
`at_kind`, saying whether that instant is real ("published", from an RSS
pubDate) or merely when we fetched it ("scraped").
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from app import publish

_COLS = """
    id INTEGER PRIMARY KEY, hash TEXT, lang TEXT, source TEXT, url TEXT,
    title TEXT, subtitle TEXT, body_text TEXT, published_date TEXT,
    published_at TEXT, scraped_at TEXT, dateline TEXT, dedup_key TEXT,
    align_json TEXT, status TEXT
"""


def _create_schema(conn, cols=_COLS):
    """The tables the export reads, as `init_db()` would create them."""
    conn.execute(f"CREATE TABLE articles ({cols})")
    conn.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, article_id INTEGER, "
                 "kind TEXT, source_url TEXT, local_path TEXT, position INT, "
                 "downloaded INT)")
    conn.execute("CREATE TABLE categories (id INTEGER PRIMARY KEY, key TEXT, "
                 "label_en TEXT, label_ko TEXT, sort INT)")
    conn.execute("CREATE TABLE article_category (article_id INT, category_id INT)")


@pytest.fixture
def db():
    """An in-memory mirror with just the tables the export reads."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    return conn


def _add(conn, hash_, *, lang="en", source="38north", published_date=None,
         published_at=None, scraped_at=None, status="fetched", title="t",
         dedup_key=None):
    conn.execute(
        "INSERT INTO articles (hash, lang, source, url, title, published_date, "
        "published_at, scraped_at, status, dedup_key) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (hash_, lang, source, f"https://example.com/{hash_}", title,
         published_date, published_at, scraped_at, status, dedup_key))


def _cards(conn, max_age_hours=0):
    stories = publish._stories(conn, publish._load_rows(conn, max_age_hours),
                               set(), "media")
    return [publish._card(s) for s in stories]


# ------------------------------------------------------------- _utc_instant
def test_naive_and_aware_datetimes_both_come_out_utc():
    assert publish._utc_instant(datetime(2026, 10, 26, 13, 24, 5)) == "2026-10-26T13:24:05Z"
    aware = datetime(2026, 10, 26, 15, 24, 5, tzinfo=timezone(timedelta(hours=2)))
    assert publish._utc_instant(aware) == "2026-10-26T13:24:05Z"


def test_sqlite_string_forms_are_understood():
    assert publish._utc_instant("2026-10-26 13:24:05.000000") == "2026-10-26T13:24:05Z"
    assert publish._utc_instant("2026-10-26T13:24:05+00:00") == "2026-10-26T13:24:05Z"
    assert publish._utc_instant("2026-10-26T13:24:05Z") == "2026-10-26T13:24:05Z"


def test_a_bare_date_is_refused_rather_than_invented():
    """Midnight UTC is exactly what produced the "16 hours ago" labels."""
    assert publish._utc_instant(date(2026, 10, 26)) is None
    assert publish._utc_instant(None) is None
    assert publish._utc_instant("") is None
    assert publish._utc_instant("not a timestamp") is None


# ------------------------------------------------------------ the card fields
def test_rss_story_carries_a_real_instant(db):
    _add(db, "rss1", published_date="2026-10-26",
         published_at="2026-10-26 09:15:00.000000",
         scraped_at="2026-10-26 12:17:00.000000")
    card = _cards(db)[0]
    assert card["date"] == "2026-10-26"
    assert card["at"] == "2026-10-26T09:15:00Z"
    assert card["at_kind"] == "published"


def test_date_only_story_is_not_given_a_fake_hour(db):
    _add(db, "rodong1", source="rodong", published_date="2026-10-26",
         scraped_at="2026-10-26 12:17:00.000000")
    card = _cards(db)[0]
    assert card["at"] == "2026-10-26T12:17:00Z"
    assert card["at_kind"] == "scraped", "front end must fall back to a day label"


def test_story_with_nothing_at_all_stays_honest(db):
    _add(db, "curated1", source="vok", published_date=None, scraped_at=None)
    card = _cards(db)[0]
    assert card["date"] is None and card["at"] is None and card["at_kind"] is None


def test_published_beats_scraped_across_the_two_language_rows(db):
    _add(db, "pair1", lang="kp", source="rodong", published_date="2026-10-26",
         scraped_at="2026-10-26 05:00:00.000000")
    _add(db, "pair1", lang="en", source="dailynk", published_date="2026-10-26",
         published_at="2026-10-26 07:30:00.000000",
         scraped_at="2026-10-26 08:00:00.000000")
    card = _cards(db)[0]
    assert card["at"] == "2026-10-26T07:30:00Z"
    assert card["at_kind"] == "published"


def test_scraped_fallback_uses_the_earliest_fetch(db):
    _add(db, "pair2", lang="kp", published_date="2026-10-26",
         scraped_at="2026-10-26 12:17:00.000000")
    _add(db, "pair2", lang="en", published_date="2026-10-26",
         scraped_at="2026-10-26 06:02:00.000000")
    assert _cards(db)[0]["at"] == "2026-10-26T06:02:00Z"


# ------------------------------------------------------------------- ordering
def test_newest_first_uses_the_instant_within_a_day(db):
    """A story published at 09:00 must not sit behind one published at 02:00
    just because of its row id."""
    _add(db, "early", published_date="2026-10-26",
         published_at="2026-10-26 02:00:00.000000")
    _add(db, "late", published_date="2026-10-26",
         published_at="2026-10-26 09:00:00.000000")
    _add(db, "dayonly-older", source="rodong", published_date="2026-10-25")
    assert [c["id"] for c in _cards(db)] == ["late", "early", "dayonly-older"]


def test_ordering_is_unchanged_for_a_database_with_no_instants(db):
    """Backfill safety: existing mirrors have no published_at at all."""
    _add(db, "a1", source="rodong", published_date="2026-10-26")
    _add(db, "a2", source="rodong", published_date="2026-10-25")
    _add(db, "a3", source="vok", published_date=None)
    assert [c["id"] for c in _cards(db)] == ["a1", "a2", "a3"]


def test_age_window_still_applies(db):
    today = datetime.now(timezone.utc).date()
    recent = (today - timedelta(days=1)).isoformat()
    ancient = (today - timedelta(days=300)).isoformat()
    _add(db, "fresh", published_date=recent)
    _add(db, "stale", published_date=ancient)
    assert [c["id"] for c in _cards(db, max_age_hours=24 * 30)] == ["fresh"]


def test_undated_curated_rows_sort_last(db):
    _add(db, "dated", source="rodong", published_date="2026-01-01")
    _add(db, "undated-a", source="vok", published_date=None)
    _add(db, "undated-b", source="magazine", published_date=None)
    ids = [c["id"] for c in _cards(db)]
    assert ids[0] == "dated" and set(ids[1:]) == {"undated-a", "undated-b"}


# ------------------------------------------------- pre-migration databases
def test_a_mirror_without_the_new_columns_still_publishes():
    """A database restored from state predates `published_at`; publish runs after
    a scrape step that may itself have been skipped, so the export has to cope
    and fall back to day labels rather than raising."""
    legacy = _COLS.replace("published_at TEXT, ", "").replace("scraped_at TEXT, ", "")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn, legacy)
    conn.execute("INSERT INTO articles (hash, lang, source, url, title, "
                 "published_date, status) VALUES ('old1','en','rodong','u','t',"
                 "'2026-10-26','fetched')")

    assert "published_at" not in publish._article_columns(conn)
    card = _cards(conn)[0]
    assert card["date"] == "2026-10-26"
    assert card["at"] is None and card["at_kind"] is None


def test_publishing_an_empty_database_still_works(tmp_path):
    """Mirrors the CI smoke test (`init_db()` then publish with no rows): the new
    column must not turn the zero-story case into a crash."""
    db = tmp_path / "kcna.db"
    conn = sqlite3.connect(db)
    _create_schema(conn)
    conn.commit()
    conn.close()

    out = tmp_path / "site"
    manifest = publish.publish(db, out, static_assets=False, clean=True)
    assert manifest["stories"] == 0
    assert json.load(open(out / "data" / "articles.json", encoding="utf-8"))["stories"] == []