"""Tests for the generic cover art used on cards with no source photo.

Two layers: the pool module (app/placeholders.py) and the export hook
(publish.py::_card), which is what actually puts art on a `cover: null` story —
common on the English RSS feeds, which rarely carry an image.
"""
import collections
import json

import pytest

from app import placeholders, publish


def _story(story_id="abc123", media=None):
    """A folded story dict in the shape publish._card() consumes."""
    return {"id": story_id, "date": "2026-10-26", "sources": ["38north"],
            "title_ko": "제목", "title_en": "A title", "body_ko": "",
            "body_en": "Body text.", "dateline": None, "subtitle": None,
            "dedup_key": None, "align": None, "categories": [],
            "media": media or [], "group": [story_id]}


@pytest.fixture
def fake_pool(tmp_path, monkeypatch):
    def make(n=50):
        d = tmp_path / "placeholders"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            (d / f"ph-{i:02d}.jpg").write_bytes(b"\xff\xd8\xff")
        monkeypatch.setattr(placeholders, "POOL_DIR", d)
        placeholders._cache = None          # drop the folder listing cache
        return d
    return make


# ---------------------------------------------------------------- selection
def test_pick_is_stable_for_one_story(fake_pool):
    fake_pool(50)
    key = "c18a1865bc1c2a02000fc14136579194"
    assert placeholders.pick(key) == placeholders.pick(key)


def test_pick_spreads_evenly_over_the_pool(fake_pool):
    fake_pool(50)
    hits = [placeholders.pick(f"id{i}") for i in range(1000)]
    assert len(set(hits)) >= 40, "a busy front page should touch most of the pool"
    counts = collections.Counter(hits)
    assert max(counts.values()) < (1000 / 50) * 4, f"lumpy spread: {counts.most_common(3)}"


def test_salt_rolls_every_assignment(fake_pool):
    fake_pool(50)
    before = {placeholders.pick(f"id{i}") for i in range(200)}
    after = {placeholders.pick(f"id{i}", salt="2026-10-26") for i in range(200)}
    assert before != after


def test_empty_pool_yields_no_cover(tmp_path, monkeypatch):
    monkeypatch.setattr(placeholders, "POOL_DIR", tmp_path / "missing")
    placeholders._cache = None
    assert placeholders.pick("abc") is None
    assert placeholders.pick_random() is None
    assert placeholders.pool_size() == 0


def test_non_images_are_ignored(fake_pool):
    d = fake_pool(3)
    (d / "notes.txt").write_text("no", encoding="utf-8")
    assert placeholders.pool_size() == 3


def test_new_files_are_picked_up_without_a_restart(fake_pool):
    d = fake_pool(10)
    (d / "ph-11.jpg").write_bytes(b"\xff\xd8\xff")
    placeholders._cache = None
    assert placeholders.pool_size() == 11


def test_urls_are_site_relative():
    """A project Pages site lives under /<repo>/, so an absolute path would
    escape the site root; assetUrl() prefixes whatever relative path it is given."""
    url = placeholders.pick("c18a1865bc1c2a02000fc14136579194")
    assert url and not url.startswith("/")
    assert url.startswith("assets/placeholders/")
    assert (placeholders.APP_DIR.parent / url).exists()


# ----------------------------------------------------- the pool in the export
def test_repo_ships_a_reviewed_pool_of_fifty():
    assert placeholders.pool_size() == 50, (
        f"expected 50 committed placeholders, found {placeholders.pool_size()} "
        f"in {placeholders.POOL_DIR} — run `python -m app.placeholders`")


def test_every_shipped_placeholder_is_a_real_image():
    for name in placeholders.pool_files():
        p = placeholders.POOL_DIR / name
        assert p.stat().st_size > placeholders.MIN_BYTES, name
        assert placeholders._looks_like_image(p.read_bytes()), name


def test_manifest_covers_every_file_and_records_rejects():
    manifest = json.loads((placeholders.POOL_DIR / "manifest.json").read_text("utf-8"))
    assert manifest["source"] == "https://picsum.photos"
    assert manifest["licence"].startswith("Lorem Picsum")
    assert {e["file"] for e in manifest["images"]} == set(placeholders.pool_files())
    for e in manifest["images"]:
        assert e["picsum_id"] and e["sha256"] and e["source_url"]
    # A photo rejected by review must never be handed out again.
    assert {e["picsum_id"] for e in manifest["images"]}.isdisjoint(
        set(manifest.get("retired_ids") or []))


# --------------------------------------------------------- publish.py::_card
def test_real_photo_wins_and_is_marked_as_such(fake_pool):
    fake_pool(50)
    card = publish._card(_story(media=[{"kind": "image", "url": "media/x/00.jpg"},
                                       {"kind": "video", "url": "media/x/v.mp4"}]))
    assert card["cover"] == "media/x/00.jpg"
    assert card["cover_kind"] == "photo"


def test_story_with_no_photo_gets_pool_art(fake_pool):
    fake_pool(50)
    card = publish._card(_story("c18a1865bc1c2a02000fc14136579194"))
    assert card["cover"] == placeholders.pick("c18a1865bc1c2a02000fc14136579194")
    assert card["cover_kind"] == "placeholder"


def test_card_cover_is_stable_across_runs(fake_pool):
    fake_pool(50)                      # two exports, same story id -> same art
    assert publish._card(_story("id-42"))["cover"] == publish._card(_story("id-42"))["cover"]


def test_no_pool_falls_back_to_the_honest_empty_state(fake_pool):
    """Without the pool the card must still say "no photo in source", not 404."""
    fake_pool(0)
    card = publish._card(_story("id-42"))
    assert card["cover"] is None and card["cover_kind"] is None


def test_detail_view_is_left_out(fake_pool):
    """Placeholders are card decoration only: the article page shows real media
    or nothing, so generic art can never read as a photo of the story."""
    fake_pool(50)
    st = _story("id-42")
    assert "cover_kind" not in publish._detail(st)
    assert publish._card(st)["cover_kind"] == "placeholder"


def test_copy_static_ships_the_pool(tmp_path):
    """`site/` is the only deployable directory, so the pool travels in assets/."""
    out = tmp_path / "site"
    out.mkdir()
    copied = publish._copy_static(out, placeholders.APP_DIR.parent)
    assert "assets/" in copied
    assert len(list((out / "assets" / "placeholders").glob("*.jpg"))) == 50
