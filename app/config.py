"""Central configuration, all overridable via environment variables."""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    """Value of an environment variable, where unset *and blank* both mean "use default".

    GitHub Actions expands an unconfigured repository variable to an empty string
    instead of leaving it unset (`${{ vars.REQUEST_DELAY }}` -> ""), and a `KEY=`
    line in .env behaves the same way. os.environ.get(name, default) then hands back
    "", which used to raise ValueError on float("") at import time and silently
    turned every SCRAPE_* switch off.
    """
    raw = os.environ.get(name)
    return default if raw is None or not raw.strip() else raw.strip()


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


# Where everything is stored (SQLite db + downloaded media). Mounted as a Docker
# volume so data survives container restarts.
DATA_DIR = Path(_env("DATA_DIR", "./data")).resolve()
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "kcna.db"
DB_URL = _env("DB_URL", f"sqlite:///{DB_PATH}")

# Source sites.
BASE_URL = _env("KCNA_BASE_URL", "http://www.kcna.kp").rstrip("/")
RODONG_BASE_URL = _env("RODONG_BASE_URL", "http://www.rodong.rep.kp").rstrip("/")
VOK_BASE_URL = _env("VOK_BASE_URL", "http://www.vok.rep.kp").rstrip("/")
# Per-site on/off switches.
SCRAPE_KCNA = _bool("SCRAPE_KCNA", True)
SCRAPE_RODONG = _bool("SCRAPE_RODONG", True)
# VOK video subtitling is heavy (ffmpeg download + Whisper transcription) and
# off by default; enable explicitly once the image is rebuilt with ffmpeg.
SCRAPE_VOK = _bool("SCRAPE_VOK", False)
# Rodong pages are large/slow; give them a longer timeout.
RODONG_TIMEOUT = _float("RODONG_TIMEOUT", 60)
# A KCNA gallery can run to a dozen-plus photos per story; cap how many we
# mirror so one photo-heavy event doesn't dominate a scheduled run.
KCNA_MAX_PHOTOS = _int("KCNA_MAX_PHOTOS", 12)

# --- External English-language sources (RSS) --------------------------------
# 38 North and Daily NK English publish full article text over WordPress RSS, so
# these are ingested from the feed rather than by crawling markup. Both sites'
# robots.txt allow the feed path. They are also *copyrighted third-party
# publications* (unlike the DPRK state feeds): each story keeps its canonical
# URL and byline, and RSS_MIRROR_FULLTEXT=0 narrows the mirror to the publisher's
# own summary plus a link out.
NORTH38_BASE_URL = _env("NORTH38_BASE_URL", "https://www.38north.org").rstrip("/")
NORTH38_FEED_PATH = _env("NORTH38_FEED_PATH", "/feed/")
DAILYNK_BASE_URL = _env("DAILYNK_BASE_URL", "https://www.dailynk.com/english").rstrip("/")
DAILYNK_FEED_PATH = _env("DAILYNK_FEED_PATH", "/english/feed/")
SCRAPE_38NORTH = _bool("SCRAPE_38NORTH", True)
SCRAPE_DAILYNK = _bool("SCRAPE_DAILYNK", True)
# Feed + asset timeout. These origins are ordinary hosted sites, so unlike
# RODONG_TIMEOUT they need no allowance for a slow state-run server.
RSS_TIMEOUT = _float("RSS_TIMEOUT", 45)
# Cap mirrored photos per feed item (38 North satellite pieces run 8-10 images).
RSS_MAX_IMAGES = _int("RSS_MAX_IMAGES", 8)
# WordPress lumps editor-chosen categories in with free post tags, so cap how
# many become nav entries per story (0 = no cap).
RSS_MAX_CATEGORIES = _int("RSS_MAX_CATEGORIES", 5)
# Mirror the full article body from content:encoded. Set to 0 to store only the
# publisher's own <description> summary and let the card link out.
RSS_MIRROR_FULLTEXT = _bool("RSS_MIRROR_FULLTEXT", True)
# These stories arrive *in English*. The site is bilingual, so this option
# generates the Korean side (EN->KO) instead of showing English-only articles.
# Off by default: it is the expensive part (long analytical pieces, keyless
# endpoint) and the viewer renders English-only stories fine.
RSS_TRANSLATE_KO = _bool("RSS_TRANSLATE_KO", False)


def rss_sources() -> list[dict]:
    """Enabled RSS sources, in scrape order."""
    catalog = [
        {"key": "38north", "label": "38 North", "base": NORTH38_BASE_URL,
         "feed": NORTH38_FEED_PATH, "enabled": SCRAPE_38NORTH},
        {"key": "dailynk", "label": "Daily NK", "base": DAILYNK_BASE_URL,
         "feed": DAILYNK_FEED_PATH, "enabled": SCRAPE_DAILYNK},
    ]
    return [s for s in catalog if s["enabled"]]


# VOK videos to subtitle (no auto-discovery yet — a curated seed list of video
# IDs from /index.php/detail_com/vi_video/<id>). Comma-separated. Each entry may
# optionally carry the Korean title as "id|한국어제목" (the detail page is
# JS-rendered, so the no-JS scraper can't pull the title itself). The English
# name is machine-translated from the Korean one.
_VOK_RAW = _env("VOK_VIDEO_IDS", "ike260528004|명작동화그림책《토끼와 사자》")
VOK_VIDEO_IDS: list[str] = []
VOK_TITLES: dict[str, str] = {}
for _e in _VOK_RAW.split(","):
    _e = _e.strip()
    if not _e:
        continue
    if "|" in _e:
        _vid, _ttl = _e.split("|", 1)
        _vid, _ttl = _vid.strip(), _ttl.strip()
    else:
        _vid, _ttl = _e, ""
    VOK_VIDEO_IDS.append(_vid)
    if _ttl:
        VOK_TITLES[_vid] = _ttl
# Whisper model size for Korean ASR (tiny|base|small|medium). Bigger = better &
# slower; "small" is a reasonable CPU default.
WHISPER_MODEL = _env("WHISPER_MODEL", "small")

# --- Magazines (korean-books.com.kp PDFs) ----------------------------------
KOREAN_BOOKS_BASE_URL = _env("KOREAN_BOOKS_BASE_URL",
                             "http://www.korean-books.com.kp").rstrip("/")
SCRAPE_MAGAZINES = _bool("SCRAPE_MAGAZINES", False)
# Issues to process, "title|pdf_url" comma-separated. (Auto-discovery is a JS/
# headless follow-up; this seed list validates the PDF pipeline first.)
_MAG_RAW = _env(
    "MAGAZINE_ISSUES",
    "금수강산 2026년 6호|http://www.korean-books.com.kp/KBMbooks/ko/periodic/mountain/20260607151203.pdf")
MAGAZINE_ISSUES: list[tuple[str, str]] = []
for _e in _MAG_RAW.split(","):
    _e = _e.strip()
    if not _e:
        continue
    if "|" in _e:
        _t, _u = _e.split("|", 1)
        MAGAZINE_ISSUES.append((_t.strip(), _u.strip()))
    else:
        MAGAZINE_ISSUES.append(("", _e))
# Cap pages processed per issue (translation cost / safety).
MAGAZINE_MAX_PAGES = _int("MAGAZINE_MAX_PAGES", 80)
# Render resolution for page images.
MAGAZINE_DPI = _int("MAGAZINE_DPI", 120)
# Hard cap (seconds) on a single ffmpeg HLS download so a hung stream can't wedge
# the run. The stream token is valid ~5 min, so downloads must be brisk anyway.
VOK_DOWNLOAD_TIMEOUT = _float("VOK_DOWNLOAD_TIMEOUT", 600)

# Self-translation (KO->EN). Rodong's own English can't be reliably matched to
# its Korean, so for Rodong we scrape Korean-only (photo articles) and generate
# the English ourselves. Provider: "google_free" (keyless public endpoint),
# "libretranslate" (self-hosted), "deepl", or "google".
TRANSLATE_PROVIDER = _env("TRANSLATE_PROVIDER", "google_free").lower()
TRANSLATE_API_KEY = _env("TRANSLATE_API_KEY", "")
TRANSLATE_API_URL = _env("TRANSLATE_API_URL", "")  # LibreTranslate endpoint / override
TRANSLATE_TARGET = _env("TRANSLATE_TARGET", "EN-US")
TRANSLATE_TIMEOUT = _float("TRANSLATE_TIMEOUT", 60)

# Only keep photo articles about self-reliance/food/agriculture/industry topics
# (see app/topics.is_on_topic). Set TOPIC_FILTER=0 to mirror everything again.
TOPIC_FILTER = _bool("TOPIC_FILTER", True)
# Category keys that should never be created, tagged, or shown in the nav.
HIDDEN_CATEGORY_KEYS = {
    "leader", "politics", "photos", "video",
    "anecdotes", "remember", "documents", "commentary", "international", "foreign",
    "c472076bded",   # "＞＞＞" (nav arrow junk)
    "c4732521769",   # "최신소식" (Latest News — duplicates the homepage default)
}

# Languages to scrape, in priority order. Phase 1 focuses on Korean; English is
# added in Phase 3 but the pipeline already supports it.
LANGS = [s.strip() for s in _env("KCNA_LANGS", "kp,en").split(",") if s.strip()]

# Politeness / reliability knobs for the slow, flaky origin.
REQUEST_TIMEOUT = _float("REQUEST_TIMEOUT", 30)
REQUEST_DELAY = _float("REQUEST_DELAY", 2.0)   # seconds between requests
MAX_RETRIES = _int("MAX_RETRIES", 4)
MAX_ARTICLES_PER_RUN = _int("MAX_ARTICLES_PER_RUN", 60)

# Only mirror content newer than this many hours. Older articles are skipped
# (and existing ones are hidden) so the mirror stays a rolling recent window.
# Default: 5 days.
MAX_AGE_HOURS = _int("MAX_AGE_HOURS", 120)

# Scheduler: how often the loop re-checks for new content.
SCRAPE_INTERVAL_MIN = _int("SCRAPE_INTERVAL_MIN", 60)

# A real browser User-Agent — the site serves only a shell to unknown agents.
USER_AGENT = _env(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

# Web server.
WEB_HOST = _env("WEB_HOST", "0.0.0.0")
WEB_PORT = _int("WEB_PORT", 8000)


def ensure_dirs() -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
