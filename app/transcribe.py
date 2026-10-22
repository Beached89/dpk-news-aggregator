"""Korean speech-to-text for VOK videos via faster-whisper (CTranslate2, CPU).

Lazy-loads the model on first use and degrades gracefully: if the dependency or
model is unavailable, transcription returns an empty list and the caller simply
holds the video (never publishes a video without subtitles).
"""
from __future__ import annotations

import logging

from . import config

log = logging.getLogger("transcribe")

_model = None
_failed = False


def ensure() -> bool:
    """Load the Whisper model once. Returns False (logged) if unavailable."""
    global _model, _failed
    if _model is not None:
        return True
    if _failed:
        return False
    try:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        log.info("whisper ready (model=%s)", config.WHISPER_MODEL)
        return True
    except Exception as e:
        _failed = True
        log.warning("whisper unavailable: %s", e)
        return False


def transcribe_korean(audio_path) -> list[dict]:
    """Transcribe a media file's Korean speech into timed segments.

    Returns a list of ``{"start": float, "end": float, "text": str}`` in order,
    or ``[]`` if the model is unavailable or no speech was detected."""
    if not ensure():
        return []
    segments, _info = _model.transcribe(
        str(audio_path), language="ko", task="transcribe",
        beam_size=5, vad_filter=True,
    )
    out: list[dict] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        out.append({"start": float(seg.start or 0.0),
                    "end": float(seg.end or 0.0),
                    "text": text})
    return out
