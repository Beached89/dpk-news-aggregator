"""Translation client tests: response parsing, batching, caching, failure modes.

Runs offline (no network, no keys) by monkeypatching the provider function; set
TRANSLATE_LIVE=1 to additionally make one real keyless request.

    python -m pytest tests/test_translate.py -q
"""
from __future__ import annotations

import json
import os
import unittest.mock as mock

import pytest

from app import config, tcache, translate

# Recorded live response from translate_a/t given 5 Korean sentences in ONE POST
# (2026-10-22, HTTP 200) — the flat-array shape the batch path depends on.
LIVE_BATCH = json.dumps([
    "Poetry activities carried out on both sides of Samripok",
    "Newly established academic institutions are actively changing their "
    "educational activities departments.",
    "I will confidently secure victory after the trial",
    "The residents of the inner city consciously take on any task and duty",
    "The superiority of the free medical care system as seen by the people",
])

NESTED_SINGLE = json.dumps([
    [["Newly founded schools", "\uc0c8\ub85c \ub3c4\ub77d\ub41c \ud559\uad00", None, None, 10]],
    None, "ko",
])


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Keyless provider, empty cache per test, no source-on-fail, no real requests."""
    monkeypatch.setattr(config, "TRANSLATE_PROVIDER", "google_free")
    monkeypatch.setattr(config, "TRANSLATE_TARGET", "EN-US")
    monkeypatch.delenv("TRANSLATE_KEEP_SOURCE_ON_FAIL", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    tcache.reset(tmp_path / "translations.json")
    translate.reset_circuit()
    yield
    tcache.reset()


def test_parse_flat_batch_is_order_preserving():
    got = translate._parse_payload(LIVE_BATCH, 5)
    assert len(got) == 5
    assert got[0].startswith("Poetry activities")
    assert got[4].startswith("The superiority")


def test_parse_nested_single_shape():
    assert translate._parse_payload(NESTED_SINGLE, 1) == ["Newly founded schools"]


def test_parse_merged_blob_for_many_inputs_is_a_failure():
    assert translate._parse_payload(json.dumps([[["en", "ko"]]]), 3) == []


def test_parse_garbage_returns_empty_not_a_crash():
    assert translate._parse_payload("[]", 1) == []
    assert translate._parse_payload('{"unexpected": 1}', 1) == []
    assert translate._parse_payload('"just a string"', 1) == ["just a string"]


def test_order_preserved_and_duplicates_requested_once():
    calls: list[list[str]] = []

    def fake(batch):
        calls.append(list(batch))
        return [f"EN-{t}" for t in batch]

    src = ["\uc0bc\ub9ac\ud3ed", "\ud559\uad00", "", "   ", "\uc0bc\ub9ac\ud3ed"]
    with mock.patch.object(translate, "_translate_google_free", fake):
        out = translate.translate_texts(src)

    assert out == ["EN-\uc0bc\ub9ac\ud3ed", "EN-\ud559\uad00", "", "   ",
                   "EN-\uc0bc\ub9ac\ud3ed"]
    # Duplicate and blank strings must not generate extra provider work.
    assert [t for c in calls for t in c] == ["\uc0bc\ub9ac\ud3ed", "\ud559\uad00"]


def test_second_call_is_served_from_cache():
    with mock.patch.object(translate, "_translate_google_free",
                           lambda b: [f"EN-{t}" for t in b]):
        translate.translate_texts(["\ubb34\ub824\ubcf4\uac74", "\ub0b4\uc8fc"])
    assert tcache.get_cache().stats()["entries"] == 2

    def boom(batch):
        raise AssertionError("cache hit should not reach the provider")

    with mock.patch.object(translate, "_translate_google_free", boom):
        out = translate.translate_texts(["\ubb34\ub824\ubcf4\uac74", "\ub0b4\uc8fc"])
    assert out == ["EN-\ubb34\ub824\ubcf4\uac74", "EN-\ub0b4\uc8fc"]


def test_cache_survives_a_restart(tmp_path):
    with mock.patch.object(translate, "_translate_google_free",
                           lambda b: ["ok"] * len(b)):
        translate.translate_texts(["\ubc30\uad00"])
    assert (tmp_path / "translations.json").exists()

    tcache.reset(tmp_path / "translations.json")      # simulated next CI run
    seen: list[list[str]] = []

    def spy(batch):
        seen.append(list(batch))
        return ["x"] * len(batch)

    with mock.patch.object(translate, "_translate_google_free", spy):
        out = translate.translate_texts(["\ubc30\uad00"])
    assert out == ["ok"] and not seen


def test_poison_line_does_not_lose_its_batch():
    batch = [f"s{i}" for i in range(10)]

    def batch_fails(texts):
        raise RuntimeError("TranslationNotFound on one line")

    def singles(text):
        if text == "s3":
            raise RuntimeError("TranslationNotFound")
        return f"EN-{text}"

    with mock.patch.object(translate, "_translate_google_free", batch_fails), \
         mock.patch.object(translate, "_translate_google_free_one", singles):
        out = translate.translate_texts(batch)

    assert len(out) == len(batch)                      # 1:1 parity holds
    assert out[3] == ""                                # untranslated, not Korean
    assert out[0] == "EN-s0" and out[9] == "EN-s9"


def test_untranslatable_line_is_never_passed_off_as_english():
    ko = "\ubaa9\ub098\ub77c \ucc3d\uac00"

    def half_dead(batch):
        return ["EN-ok" if t == "keep" else "" for t in batch]

    with mock.patch.object(translate, "_translate_google_free", half_dead):
        out = translate.translate_texts(["keep", ko])
    assert out == ["EN-ok", ""]


def test_keep_source_flag_restores_old_behaviour(monkeypatch):
    monkeypatch.setenv("TRANSLATE_KEEP_SOURCE_ON_FAIL", "1")
    ko = "\ubaa9\ub098\ub77c \ucc3d\uac00"

    def half_dead(batch):
        return ["EN-ok" if t == "keep" else "" for t in batch]

    with mock.patch.object(translate, "_translate_google_free", half_dead):
        out = translate.translate_texts(["keep", ko])
    assert out == ["EN-ok", ko]


def test_everything_failing_raises_so_the_caller_retries_later():
    ko = "\uc804\ubd80 \uc2e4\ud328"
    with mock.patch.object(translate, "_translate_google_free",
                           side_effect=RuntimeError("boom")), \
         mock.patch.object(translate, "_translate_google_free_one",
                           side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            translate.translate_texts([ko])


def test_circuit_breaker_stops_hammering():
    attempts: list[str] = []

    def singles_fail(text):
        attempts.append(text)
        raise RuntimeError("429 rate limited")

    with mock.patch.object(translate, "_translate_google_free",
                           side_effect=RuntimeError("429 rate limited")), \
         mock.patch.object(translate, "_translate_google_free_one",
                           side_effect=singles_fail):
        with pytest.raises(RuntimeError):
            translate.translate_texts([f"s{i}" for i in range(60)])

    # Trips after _BREAKER_TRIPS lines, and the following batches must not issue
    # any further per-line attempts against a refusing endpoint.
    assert len(attempts) == translate._BREAKER_TRIPS
    assert translate._blocked["google_free"] is True


@pytest.mark.skipif(os.environ.get("TRANSLATE_LIVE") != "1",
                    reason="set TRANSLATE_LIVE=1 to hit the keyless endpoint")
def test_live_batched_keyless_request():
    src = ["\uc0bc\ub9ac\ud3ed \ub450\uba74\uc5d0\uc11c \ucc3d\uace0\ud55c \uc2dc\ubb38\ud65c\ub3d9",
           "\ub0b4\uc8fc \uc0b0\uc18c\ub294 \uc790\uac01\uc131\ub418\ub3c4\ub85d \ub2e4\u00ac\ub2e4"]
    out = translate._translate_google_free(src)
    assert len(out) == 2
    assert all(o.strip() for o in out)

