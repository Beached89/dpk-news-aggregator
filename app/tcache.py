"""Persistent, content-addressed KO->EN translation cache.

Why this exists: the free translation paths (Google's keyless endpoint, DeepL's
free tier) are rate- and quota-limited, and the pipeline now runs on throwaway
CI runners that start with an empty disk every time. Without a cache, each run
re-translates the sentences it already translated yesterday — a Rodong article
stays inside the scrape window for days, so the same paragraphs get requested on
every single run. That is exactly how a free tier gets burned on duplicate work.

With the cache, steady-state translation volume drops to "only the sentences
that are new", which is what makes the free tiers viable at all. The cache file
travels with the SQLite database between runs (see the R2 sync steps in
`.github/workflows/scrape.yml`).

Keys are a hash of provider + target + source text, so entries produced by
different providers or into different targets can safely coexist in one file,
and switching `TRANSLATE_PROVIDER` never serves a stale mismatch.

Stdlib-only on purpose: this module is on the hot path of the export job, which
must run without torch/kiwipiepy/httpx installed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from . import config

log = logging.getLogger("tcache")

_VERSION = 1


class TranslationCache:
    """A JSON dict of sha1(provider|target|text) -> translated text.

    Writes are deferred and atomic (tmp + os.replace) because a run may be
    interrupted at any point, and a truncated cache file must never take the
    scrape down with it. A cache is strictly an optimisation: every failure here
    is logged and swallowed so translation still happens the slow way.
    """

    def __init__(self, path: Path | str, *, max_entries: int = 100_000,
                 readonly: bool = False) -> None:
        self.path = Path(path)
        self.max_entries = max_entries
        self.readonly = readonly
        self._lock = threading.Lock()
        self._entries: dict[str, str] = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            # Corrupt or partially written: keep a copy for forensics, start clean.
            log.warning("translation cache unreadable (%s); starting empty", e)
            try:
                self.path.replace(self.path.with_suffix(".json.bak"))
            except OSError:
                pass
            return
        entries = raw.get("entries") if isinstance(raw, dict) else raw
        if isinstance(entries, dict):
            self._entries = {k: v for k, v in entries.items() if isinstance(v, str)}
            log.info("loaded %d cached translations from %s",
                     len(self._entries), self.path.name)

    def flush(self, force: bool = False) -> bool:
        """Persist if dirty. Returns True when a write happened."""
        with self._lock:
            if not self._dirty and not force:
                return False
            if self.readonly:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": _VERSION, "count": len(self._entries),
                       "entries": self._entries}
            tmp = self.path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(payload, ensure_ascii=False),
                               encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as e:
                log.warning("could not write translation cache: %s", e)
                return False
            self._dirty = False
            log.info("saved %d cached translations to %s",
                     len(self._entries), self.path.name)
            return True


    # --------------------------------------------------------------- access
    @staticmethod
    def key(text: str, target: str | None = None) -> str:
        """sha1(provider|target|text).

        ``target`` overrides ``TRANSLATE_TARGET``. The RSS sources translate
        *English -> Korean* while everything else is Korean -> English, and both
        directions share one cache file; passing the direction keeps the two
        populations from being treated as interchangeable entries.
        """
        prov = config.TRANSLATE_PROVIDER
        tgt = target or config.TRANSLATE_TARGET
        return hashlib.sha1(f"{prov}|{tgt}|{text}".encode("utf-8")).hexdigest()

    def get(self, text: str, *, target: str | None = None) -> Optional[str]:
        with self._lock:
            val = self._entries.get(self.key(text, target))
        if val:
            self.hits += 1
        else:
            self.misses += 1
        return val

    def put(self, text: str, translation: str, *, target: str | None = None) -> None:
        if not translation or not translation.strip():
            return                     # never cache an empty/failure result
        with self._lock:
            k = self.key(text, target)
            if self._entries.get(k) == translation:
                return
            self._entries[k] = translation
            self._dirty = True
            # News sentences rarely recur, so dropping the oldest insertions is
            # the cheapest way to keep the file (and the R2 object) bounded.
            overflow = len(self._entries) - self.max_entries
            if overflow > 0:
                for stale in list(self._entries.keys())[:overflow]:
                    self._entries.pop(stale, None)
                log.info("pruned %d oldest cached translations (cap %d)",
                         overflow, self.max_entries)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"entries": len(self._entries), "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else None}


_default: Optional[TranslationCache] = None


def get_cache(readonly: bool = False) -> TranslationCache:
    """Process-wide cache at TRANSLATION_CACHE_PATH (default data/translations.json)."""
    global _default
    if _default is None:
        path = os.environ.get("TRANSLATION_CACHE_PATH", "").strip()
        _default = TranslationCache(path or (config.DATA_DIR / "translations.json"),
                                    max_entries=int(os.environ.get(
                                        "TRANSLATION_CACHE_MAX", "100000")),
                                    readonly=readonly)
    return _default


def reset(path: str | Path | None = None) -> None:
    """Drop the singleton (used by tests and by callers overriding the path)."""
    global _default
    _default = TranslationCache(path) if path is not None else None
