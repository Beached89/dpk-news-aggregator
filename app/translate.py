"""Cloud machine-translation client (KO->EN) for the self-translated Rodong feed.

Rodong's own English edition can't be reliably aligned to its Korean (separate
per-language IDs, no link, no matchable photo for text pieces), so we scrape
Korean-only and generate the English ourselves. Supported providers: DeepL and
Google Cloud Translation v2, selected by config and keyed from the environment.

`translate_texts` preserves input order 1:1, so callers can translate a list of
Korean sentences and zip the results straight back onto them.
"""
from __future__ import annotations

import logging
import time

import httpx

from . import config

log = logging.getLogger("translate")

_MAX_BATCH = 45  # DeepL allows up to 50 texts/request; stay safely under.


def available() -> bool:
    if config.TRANSLATE_PROVIDER == "google_free":
        return True  # keyless public endpoint
    # LibreTranslate (self-hosted) needs only a URL; cloud providers need a key.
    if config.TRANSLATE_PROVIDER == "libretranslate":
        return bool(config.TRANSLATE_API_URL)
    return bool(config.TRANSLATE_API_KEY)


def _translate_google_free(texts: list[str]) -> list[str]:
    """Google Translate's free public endpoint via deep-translator (no key).
    Translates per sentence with light throttling to avoid rate-limiting.

    Resilient per line: the unofficial endpoint raises TranslationNotFound on
    some inputs (very short, poetic, or song lines — common in video
    transcripts). One such failure must not abort the whole batch, so we retry
    once and then fall back to the original Korean for that line."""
    from deep_translator import GoogleTranslator
    tr = GoogleTranslator(source="ko", target="en")
    out = []
    for i, t in enumerate(texts):
        if i:
            time.sleep(0.3)  # be gentle on the unofficial endpoint
        res = None
        for attempt in range(2):
            try:
                res = tr.translate(t)
                break
            except Exception as e:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                log.warning("google_free: line untranslatable, keeping source (%s)",
                            str(e)[:80])
        out.append(res or t)
    return out


def _deepl_url() -> str:
    if config.TRANSLATE_API_URL:
        return config.TRANSLATE_API_URL
    # Free-tier keys end in ":fx" and use a different host.
    if config.TRANSLATE_API_KEY.endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


def _translate_deepl(texts: list[str]) -> list[str]:
    headers = {"Authorization": f"DeepL-Auth-Key {config.TRANSLATE_API_KEY}"}
    data = [("source_lang", "KO"), ("target_lang", config.TRANSLATE_TARGET)]
    data += [("text", t) for t in texts]
    r = httpx.post(_deepl_url(), data=data, headers=headers,
                   timeout=config.TRANSLATE_TIMEOUT)
    r.raise_for_status()
    return [t["text"] for t in r.json()["translations"]]


def _translate_libretranslate(texts: list[str]) -> list[str]:
    url = config.TRANSLATE_API_URL or "http://localhost:5000/translate"
    target = config.TRANSLATE_TARGET.split("-")[0].lower()  # EN-US -> en
    payload = {"q": texts, "source": "ko", "target": target, "format": "text"}
    if config.TRANSLATE_API_KEY:               # only if the instance requires one
        payload["api_key"] = config.TRANSLATE_API_KEY
    r = httpx.post(url, json=payload, timeout=config.TRANSLATE_TIMEOUT)
    r.raise_for_status()
    tt = r.json()["translatedText"]
    return [tt] if isinstance(tt, str) else list(tt)


def _translate_google(texts: list[str]) -> list[str]:
    url = config.TRANSLATE_API_URL or "https://translation.googleapis.com/language/translate/v2"
    target = config.TRANSLATE_TARGET.split("-")[0].lower()  # EN-US -> en
    r = httpx.post(url, params={"key": config.TRANSLATE_API_KEY},
                   json={"q": texts, "source": "ko", "target": target, "format": "text"},
                   timeout=config.TRANSLATE_TIMEOUT)
    r.raise_for_status()
    return [t["translatedText"] for t in r.json()["data"]["translations"]]


def translate_texts(texts: list[str]) -> list[str]:
    """Translate Korean strings to English, preserving order 1:1.

    Raises on hard failure (no key, HTTP error) so the caller can skip storing a
    translation this run and retry later. Empty/whitespace inputs pass through.
    """
    if not texts:
        return []
    if not available():
        raise RuntimeError("translation requested but TRANSLATE_API_KEY is unset")

    fn = {"google": _translate_google,
          "google_free": _translate_google_free,
          "libretranslate": _translate_libretranslate}.get(
              config.TRANSLATE_PROVIDER, _translate_deepl)
    out: list[str] = []
    for i in range(0, len(texts), _MAX_BATCH):
        batch = texts[i:i + _MAX_BATCH]
        # Don't waste quota on blank strings; translate only the non-empty ones.
        idx = [j for j, t in enumerate(batch) if t.strip()]
        translated = fn([batch[j] for j in idx]) if idx else []
        merged = list(batch)
        for j, tr in zip(idx, translated):
            merged[j] = tr
        out.extend(merged)
    if len(out) != len(texts):
        raise RuntimeError(f"translation count mismatch: {len(out)} != {len(texts)}")
    return out
