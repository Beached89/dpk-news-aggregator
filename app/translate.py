"""Cloud machine-translation client (KO->EN) for the self-translated feeds.

Rodong's own English edition can't be reliably aligned to its Korean (separate
per-language IDs, no link, no matchable photo for text pieces), so we scrape
Korean-only and generate the English ourselves. Providers: the keyless public
Google endpoint (default), DeepL, Google Cloud Translation v2, or LibreTranslate.

Two properties callers depend on:

* `translate_texts` preserves input order 1:1, so a list of Korean sentences can
  be zipped straight back onto itself.
* A hard failure raises, so the caller can skip storing a translation this run
  and retry later instead of persisting garbage.

Design notes for running on free tiers from CI (see README "Translation"):

* Batched. The public endpoint accepts several `q=` params per request and
  returns one string per input, in order (verified live: 5 sentences in a single
  POST, HTTP 200). Batching 45 sentences per request turns thousands of calls
  per run into tens, which is the difference between surviving and being
  throttled from a shared datacenter IP.
* Cached. Sentences already seen are served from `app.tcache` before any request
  goes out, so re-running over the same rolling window costs nothing.
* No silent Korean-as-English. A sentence that cannot be translated comes back
  as "" (visibly untranslated) rather than the Korean source, which a learner
  would misread as English. Set TRANSLATE_KEEP_SOURCE_ON_FAIL=1 to restore the
  old fallback.
* Circuit breaker. Once the endpoint looks dead or rate-limited we stop trying
  for the rest of the run instead of hammering it sentence by sentence.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, tcache

log = logging.getLogger("translate")

_MAX_BATCH = 45        # sentences per request; the endpoint takes more, stay modest
_MAX_ATTEMPTS = 3      # per request, on 429/5xx
_BREAKER_TRIPS = 5     # consecutive per-sentence failures before giving up on a run
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# The unofficial, keyless endpoints. translate_a/t is the batch-capable one;
# translate_a/single is per-text, kept only as a fallback for a poison line.
_G_BATCH = "https://translate.googleapis.com/translate_a/t"
_G_SINGLE = "https://translate.googleapis.com/translate_a/single"

# Set once the keyless endpoint has refused us repeatedly, so a dead endpoint
# costs one bad batch instead of an hour of retries across hundreds of them.
_blocked = {"google_free": False}


def _keep_source_on_fail() -> bool:
    return os.environ.get("TRANSLATE_KEEP_SOURCE_ON_FAIL", "").lower() in (
        "1", "true", "yes", "on")


def reset_circuit() -> None:
    """Re-arm the keyless endpoint (called at the start of each translate_texts)."""
    _blocked["google_free"] = False


def available() -> bool:
    if config.TRANSLATE_PROVIDER == "google_free":
        return not _blocked["google_free"]      # keyless public endpoint
    # LibreTranslate (self-hosted) needs only a URL; cloud providers need a key.
    if config.TRANSLATE_PROVIDER == "libretranslate":
        return bool(config.TRANSLATE_API_URL)   # self-hosted needs only a URL
    return bool(config.TRANSLATE_API_KEY)


# --------------------------------------------------------------- transport
def _post_form(url: str, pairs: list[tuple[str, str]]) -> str:
    """POST a urlencoded form, stdlib only, retrying 429/5xx with backoff.

    stdlib rather than httpx so the translation path carries no third-party
    dependency: `deep-translator` only ever sent one sentence per request and is
    gone, and the export/translate jobs run on a bare interpreter.
    """
    body = urllib.parse.urlencode(pairs).encode("utf-8")
    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"User-Agent": _UA,
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=config.TRANSLATE_TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1) ** 2)   # back off, then retry
                continue
            raise                       # 403/451 won't improve by retrying
        except Exception as e:  # noqa: BLE001  (timeout, DNS, connection reset)
            last = e
            time.sleep(1.5 * (attempt + 1) ** 2)
    raise RuntimeError(
        f"translate request failed after {_MAX_ATTEMPTS} attempts: {last}")


def _parse_payload(raw: str, expected: int) -> list[str]:
    """Pull the translated strings out of a translate_a response body.

    Verified live shape for a batched translate_a/t call is a flat JSON array of
    strings, one per input, in request order:

        ["Poetry activities carried out on both sides of Samripok", "Newly ..."]

    translate_a/single (and older/param variants) instead nest segments as
    [[["en", "ko", null, null, n], ...], ...]. Accept both, and return [] on
    anything else so a format change degrades to a count mismatch — which the
    caller handles — rather than publishing a mangled translation.
    """
    data = json.loads(raw)
    if isinstance(data, str):
        return [data]
    if not isinstance(data, list) or not data:
        return []
    first = data[0]
    if isinstance(first, str):
        return [x if isinstance(x, str) else "" for x in data]
    if isinstance(first, list):
        # translate_a/single: data[0] holds the segment chunks of ONE result and
        # the entries after it are metadata (detected language etc.), which must
        # not be mistaken for more translations.
        chunks = [s[0] for s in first
                  if isinstance(s, list) and s and isinstance(s[0], str)]
        if not chunks:
            return []
        if expected > 1:
            # One merged blob tells us nothing per sentence; treat as failure.
            log.warning("google_free: %d inputs came back as 1 merged result",
                        expected)
            return []
        return ["".join(chunks)]
    return []


def _google_qs(target: str) -> str:
    return urllib.parse.urlencode({"client": "gtx", "sl": "ko", "tl": target,
                                   "dt": "t"})


def _translate_google_free(texts: list[str]) -> list[str]:
    """Batched keyless Google translation: one request per _MAX_BATCH sentences."""
    target = config.TRANSLATE_TARGET.split("-")[0].lower()   # EN-US -> en
    url = f"{_G_BATCH}?{_google_qs(target)}"
    raw = _post_form(url, [("q", t) for t in texts])
    out = _parse_payload(raw, len(texts))
    if len(out) != len(texts):
        raise RuntimeError(
            f"google_free batch shape mismatch ({len(out)} for {len(texts)})")
    return out


def _translate_google_free_one(text: str) -> str:
    """Per-sentence fallback for a batch that failed as a whole.

    A single untranslatable line (very short, poetic, or a song line — common in
    video transcripts) must not cost us the other 44 sentences in its batch.
    """
    target = config.TRANSLATE_TARGET.split("-")[0].lower()
    url = f"{_G_SINGLE}?{_google_qs(target)}"
    raw = _post_form(url, [("q", text)])
    out = _parse_payload(raw, 1)
    if not out or not out[0].strip():
        raise RuntimeError("google_free: empty result")
    return out[0]


# ------------------------------------------------------- keyed HTTP providers
def _deepl_url() -> str:
    if config.TRANSLATE_API_URL:
        return config.TRANSLATE_API_URL
    # Free-tier keys end in ":fx" and use a different host.
    if config.TRANSLATE_API_KEY.endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


def _translate_deepl(texts: list[str]) -> list[str]:
    import httpx
    headers = {"Authorization": f"DeepL-Auth-Key {config.TRANSLATE_API_KEY}"}
    data = [("source_lang", "KO"), ("target_lang", config.TRANSLATE_TARGET)]
    data += [("text", t) for t in texts]
    r = httpx.post(_deepl_url(), data=data, headers=headers,
                   timeout=config.TRANSLATE_TIMEOUT)
    r.raise_for_status()
    return [t["text"] for t in r.json()["translations"]]


def _translate_libretranslate(texts: list[str]) -> list[str]:
    import httpx
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
    import httpx
    url = config.TRANSLATE_API_URL or "https://translation.googleapis.com/language/translate/v2"
    target = config.TRANSLATE_TARGET.split("-")[0].lower()  # EN-US -> en
    r = httpx.post(url, params={"key": config.TRANSLATE_API_KEY},
                   json={"q": texts, "source": "ko", "target": target,
                         "format": "text"},
                   timeout=config.TRANSLATE_TIMEOUT)
    r.raise_for_status()
    return [t["translatedText"] for t in r.json()["data"]["translations"]]


# Provider name -> the module-level function that serves it. Resolved lazily in
# _provider_fn() rather than stored as objects, so the functions can be patched
# (tests) or redefined without rebuilding this map.
_PROVIDERS = {"google": "_translate_google",
              "google_free": "_translate_google_free",
              "libretranslate": "_translate_libretranslate"}
_FALLBACK_PROVIDER = "_translate_deepl"


def _provider_fn():
    """The configured batch translator; unknown names fall through to DeepL."""
    return globals()[_PROVIDERS.get(config.TRANSLATE_PROVIDER,
                                    _FALLBACK_PROVIDER)]


def _one_by_one(texts: list[str]) -> list[str | None]:
    """Salvage what we can from a batch the provider rejected as a whole.

    Only the keyless provider has a per-text path; for the keyed APIs a batch
    failure is an account/network problem where repeating the same request 45
    times just burns quota. Light throttling, and a circuit breaker so a dead
    endpoint costs us one trip instead of one hour.
    """
    out: list[str | None] = []
    fails = 0
    for i, t in enumerate(texts):
        if _blocked["google_free"]:
            out.extend([None] * (len(texts) - len(out)))
            log.error("google_free: circuit open, skipping %d remaining sentences",
                      len(texts) - len(out))
            return out
        if i:
            time.sleep(0.3)                    # be gentle on the unofficial endpoint
        try:
            out.append(_translate_google_free_one(t))
            fails = 0
        except Exception as e:  # noqa: BLE001
            fails += 1
            out.append(None)
            log.warning("google_free: line untranslatable (%s)", str(e)[:90])
            if fails >= _BREAKER_TRIPS:
                _blocked["google_free"] = True
    return out




def translate_texts(texts: list[str], *, use_cache: bool = True) -> list[str]:
    """Translate Korean strings to English, preserving input order 1:1.

    Cache-first: anything already in `app.tcache` costs no request, and the
    remaining strings are de-duplicated (a repeated sentence inside one body is
    translated once) and sent in batches of _MAX_BATCH.

    Failure handling, deliberately asymmetric:

    * A whole batch that errors or comes back the wrong length is retried one
      sentence at a time, so a single poison line cannot cost the other 44.
    * A sentence that still fails is returned as "" — visibly untranslated.
      (The previous behaviour returned the Korean source, which is a bug on a
      site whose whole point is showing learners an English rendering.)
    * Only if *nothing* could be translated does this raise, which is the signal
      callers use to skip storing a translation and retry on the next run.
    * Length parity is structural: `out` starts as a copy of the input and is
      filled in place, so the old end-of-function count check can no longer be
      the thing that catches a short response.
    """
    if not texts:
        return []
    reset_circuit()
    if not available():
        raise RuntimeError("translation requested but TRANSLATE_API_KEY is unset")

    provider = config.TRANSLATE_PROVIDER
    fn = _provider_fn()
    cache = tcache.get_cache() if use_cache else None

    out: list[str] = list(texts)        # blank strings pass through untouched
    need: dict[str, list[int]] = {}     # source text -> positions it appears at
    for i, t in enumerate(texts):
        if not t.strip():
            continue
        hit = cache.get(t) if cache is not None else None
        if hit:
            out[i] = hit
        else:
            need.setdefault(t, []).append(i)

    if not need:
        log.info("translation: %d strings, all served from cache", len(texts))
        return out

    uniq = list(need)
    failed = 0
    for start in range(0, len(uniq), _MAX_BATCH):
        batch = uniq[start:start + _MAX_BATCH]
        results: list[str | None]
        if provider == "google_free" and _blocked["google_free"]:
            results = [None] * len(batch)          # don't keep poking a dead endpoint
        else:
            try:
                results = fn(batch)
                if len(results) != len(batch):
                    raise RuntimeError(
                        f"{len(results)} results for {len(batch)} inputs")
            except Exception as e:  # noqa: BLE001
                log.warning("translation batch of %d failed (%s); retrying per line",
                            len(batch), str(e)[:120])
                results = (_one_by_one(batch) if provider == "google_free"
                           else [None] * len(batch))
        for src, en in zip(batch, results):
            if en and en.strip():
                if cache is not None:
                    cache.put(src, en)
                for pos in need[src]:
                    out[pos] = en
            else:
                failed += 1
                keep = src if _keep_source_on_fail() else ""
                for pos in need[src]:
                    out[pos] = keep

    if cache is not None:
        cache.flush()
        log.info("translation cache %s", cache.stats())
    if failed:
        log.warning("%d/%d distinct strings untranslated this run (%s)",
                    failed, len(uniq),
                    "kept Korean" if _keep_source_on_fail() else "left empty")
    if failed == len(uniq):
        raise RuntimeError(f"translation failed for all {failed} strings")
    return out


# ------------------------------------------------------------ reverse direction
# 38 North and Daily NK publish *in English*, so for those the machine-generated
# side is Korean. This is deliberately its own function rather than a flag on
# translate_texts(): the KO->EN path is what the whole site depends on, and its
# failure semantics are the opposite of these sources' (a Rodong article stays
# hidden until English exists; an English article is already complete in its own
# language and publishes whether or not Korean ever materialises).
_KO_TARGET = "ko"
_EN_SOURCE = "en"
_REVERSE_MAX_BATCH = 12      # these are whole paragraphs, not sentences


def reverse_available() -> bool:
    """Can the configured provider serve EN->KO?"""
    provider = config.TRANSLATE_PROVIDER
    if provider == "google_free":
        return not _blocked["google_free"]        # keyless, any direction
    if provider == "libretranslate":
        return bool(config.TRANSLATE_API_URL)     # if the image has a ko model
    return bool(config.TRANSLATE_API_KEY)


def _reverse_batch(texts: list[str]) -> list[str]:
    """One EN->KO request for a batch of strings, in the configured provider."""
    provider = config.TRANSLATE_PROVIDER
    if provider == "google_free":
        query = urllib.parse.urlencode({"client": "gtx", "sl": _EN_SOURCE,
                                        "tl": _KO_TARGET, "dt": "t"})
        raw = _post_form(f"{_G_BATCH}?{query}", [("q", t) for t in texts])
        out = _parse_payload(raw, len(texts))
        if len(out) != len(texts):
            raise RuntimeError(f"{len(out)} results for {len(texts)} inputs")
        return out
    import httpx
    if provider == "deepl":
        headers = {"Authorization": f"DeepL-Auth-Key {config.TRANSLATE_API_KEY}"}
        data = [("source_lang", "EN"), ("target_lang", "KO")]
        data += [("text", t) for t in texts]
        r = httpx.post(_deepl_url(), data=data, headers=headers,
                       timeout=config.TRANSLATE_TIMEOUT)
        r.raise_for_status()
        return [t["text"] for t in r.json()["translations"]]
    if provider == "libretranslate":
        url = config.TRANSLATE_API_URL or "http://localhost:5000/translate"
        payload = {"q": texts, "source": _EN_SOURCE, "target": _KO_TARGET,
                   "format": "text"}
        if config.TRANSLATE_API_KEY:
            payload["api_key"] = config.TRANSLATE_API_KEY
        r = httpx.post(url, json=payload, timeout=config.TRANSLATE_TIMEOUT)
        r.raise_for_status()
        translated = r.json()["translatedText"]
        return [translated] if isinstance(translated, str) else list(translated)
    # "google" (Cloud Translation) and any other key-authenticated endpoint.
    url = config.TRANSLATE_API_URL or (
        "https://translation.googleapis.com/language/translate/v2")
    r = httpx.post(url, params={"key": config.TRANSLATE_API_KEY},
                   json={"q": texts, "source": _EN_SOURCE, "target": _KO_TARGET,
                         "format": "text"}, timeout=config.TRANSLATE_TIMEOUT)
    r.raise_for_status()
    return [t["translatedText"] for t in r.json()["data"]["translations"]]


def translate_to_ko(texts: list[str], *, use_cache: bool = True) -> list[str]:
    """English strings -> Korean, order preserved 1:1 with the input.

    Mirrors translate_texts(): cache-first (under the ``ko`` target so entries
    can't be mistaken for KO->EN ones), de-duplicated, batched, "" where a string
    could not be translated, and a raise only when nothing at all came through -
    which is the caller's cue to publish English-only and retry on the next run.
    """
    if not texts:
        return []
    if not reverse_available():
        raise RuntimeError(
            f"{config.TRANSLATE_PROVIDER} cannot do EN->KO without "
            f"TRANSLATE_API_KEY/TRANSLATE_API_URL")

    cache = tcache.get_cache() if use_cache else None
    out: list[str] = list(texts)
    need: dict[str, list[int]] = {}
    for i, text in enumerate(texts):
        if not text.strip():
            continue
        hit = cache.get(text, target=_KO_TARGET) if cache is not None else None
        if hit:
            out[i] = hit
        else:
            need.setdefault(text, []).append(i)
    if not need:
        log.info("en->ko: %d strings, all served from cache", len(texts))
        return out

    uniq = list(need)
    failed = 0
    for start in range(0, len(uniq), _REVERSE_MAX_BATCH):
        batch = uniq[start:start + _REVERSE_MAX_BATCH]
        try:
            results = _reverse_batch(batch)
            if len(results) != len(batch):
                raise RuntimeError(f"{len(results)} results for {len(batch)} inputs")
        except Exception as e:  # noqa: BLE001
            log.warning("en->ko batch of %d failed (%s); retrying per line",
                        len(batch), str(e)[:120])
            results = []
            for text in batch:
                try:
                    results.append(_reverse_batch([text])[0])
                except Exception as line_error:  # noqa: BLE001
                    results.append(None)
                    log.warning("en->ko: line failed (%s)", str(line_error)[:90])
                time.sleep(0.3)                  # gentle on the keyless endpoint
        for source_text, korean in zip(batch, results):
            if korean and korean.strip():
                if cache is not None:
                    cache.put(source_text, korean, target=_KO_TARGET)
                for pos in need[source_text]:
                    out[pos] = korean
            else:
                failed += 1
                for pos in need[source_text]:
                    out[pos] = ""

    if cache is not None:
        cache.flush()
    if failed:
        log.warning("en->ko: %d/%d distinct strings untranslated", failed, len(uniq))
    if failed == len(uniq):
        raise RuntimeError(f"en->ko failed for all {failed} strings")
    return out
