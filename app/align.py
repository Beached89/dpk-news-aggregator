"""Word-level Korean<->English alignment via SimAlign (multilingual BERT).

Used by the learning view to highlight just the English word(s) that correspond
to a hovered Korean word. This is heavy (PyTorch + a multilingual BERT model),
so it is loaded lazily and fails *soft*: if the model can't load, `ensure()`
returns False and callers skip alignment, leaving the sentence-level highlight.
"""
from __future__ import annotations

import logging

log = logging.getLogger("align")

_aligner = None
_tried = False
_ok = False


def ensure() -> bool:
    """Load the aligner once. Returns True if alignment is usable."""
    global _aligner, _tried, _ok
    if _tried:
        return _ok
    _tried = True
    try:
        from simalign import SentenceAligner
        # "inter" matching = high-precision intersection of fwd/bwd alignments.
        _aligner = SentenceAligner(model="bert", token_type="bpe", matching_methods="i")
        _ok = True
        log.info("word alignment ready (SimAlign / multilingual BERT)")
    except Exception as e:
        _ok = False
        log.warning("word alignment unavailable (%s) — using sentence-level highlight", e)
    return _ok


def align_sentence(ko_tokens: list[str], en_tokens: list[str]) -> dict[int, list[int]]:
    """Map Korean token index -> list of English token indices. Empty on failure."""
    if not _ok or not ko_tokens or not en_tokens:
        return {}
    try:
        res = _aligner.get_word_aligns(ko_tokens, en_tokens)
        pairs = res.get("inter") or next(iter(res.values()), [])
        out: dict[int, list[int]] = {}
        for i, j in pairs:
            out.setdefault(int(i), []).append(int(j))
        return out
    except Exception as e:
        log.warning("alignment failed for one sentence: %s", e)
        return {}
