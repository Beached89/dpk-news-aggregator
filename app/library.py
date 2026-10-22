"""One-time fetch + hash-verify of the official DPRK language texts.

Downloads the 조선어 · Hangul/Chosŏn'gŭl reading-room PDFs (Internet Archive item
"letslearnkorean", Foreign Languages Publishing House, 1995) into
MEDIA_DIR/_library/<subdir>/, SHA-256s each file, and records the hashes in a
manifest. On later runs it re-hashes the files on disk and only re-downloads the
ones that are missing or fail their recorded hash — so this is effectively a
one-time step at container build/first-run, not a recurring fetch.

Runs as its own one-shot container (which, unlike the build sandbox, has
outbound internet). Best-effort: a download failure is logged and the process
still exits 0, so the rest of the stack can come up (the /hangul page falls back
to the Internet Archive viewer for any text that isn't self-hosted yet).
"""
from __future__ import annotations

import hashlib
import json
import logging
from urllib.parse import quote

import httpx

from . import config

log = logging.getLogger("library")

ARCHIVE_ID = "letslearnkorean"
SUBDIR = "pyongyang-dialect"
DOWNLOAD_BASE = "https://archive.org/download"

# (local filename, remote filename on the Internet Archive)
FILES = [
    ("Lets-Learn-Korean-1.pdf", "Let's Learn Korean 1-of-4_rotated_text.pdf"),
    ("Lets-Learn-Korean-2.pdf", "Let's Learn Korean 2-of-4_rotated_text.pdf"),
    ("Lets-Learn-Korean-3.pdf", "Let's Learn Korean 3-of-4_rotated_text.pdf"),
    ("Lets-Learn-Korean-4.pdf", "Let's Learn Korean 4-of-4_rotated_text.pdf"),
    ("Learn-Korean-on-Your-Own.pdf", "Learn Korean On Your Own_text.pdf"),
    ("Choson-O-1.pdf", "Choson_O_1_text.pdf"),
]


def _dir():
    return config.MEDIA_DIR / "_library" / SUBDIR


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(remote: str, dest) -> bool:
    url = f"{DOWNLOAD_BASE}/{ARCHIVE_ID}/{quote(remote)}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=180.0) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.replace(dest)
        return True
    except Exception as e:
        log.warning("library: download failed for %s: %s", dest.name, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def fetch(force: bool = False) -> int:
    """Ensure all texts are present and hash-valid. Returns the count present."""
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    manifest_path = d / "manifest.json"
    manifest: dict = {}
    if manifest_path.exists() and not force:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    changed = False
    for local, remote in FILES:
        fp = d / local
        recorded = manifest.get(local)
        if not force and fp.exists() and fp.stat().st_size > 0:
            if recorded is None:
                # Present but never hashed (e.g., user dropped it in) — record it.
                manifest[local] = _sha256(fp)
                changed = True
                log.info("library: %s present, hash recorded", local)
                continue
            if _sha256(fp) == recorded:
                log.info("library: %s ok (hash verified)", local)
                continue
            log.warning("library: %s failed hash check — re-downloading", local)
        log.info("library: fetching %s", local)
        if _download(remote, fp) and fp.exists() and fp.stat().st_size > 0:
            manifest[local] = _sha256(fp)
            changed = True
            log.info("library: saved %s (%d bytes)", local, fp.stat().st_size)

    if changed:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    present = sum(1 for local, _ in FILES if (d / local).exists() and (d / local).stat().st_size > 0)
    log.info("library: %d/%d texts present and verified", present, len(FILES))
    return present


# PDF.js is vendored locally so the reading room renders PDFs with our own
# (self-hosted) viewer instead of the browser's built-in one.
PDFJS_VERSION = "3.11.174"
PDFJS_FILES = ["pdf.min.js", "pdf.worker.min.js"]
PDFJS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js"


def fetch_pdfjs() -> int:
    d = config.MEDIA_DIR / "_assets" / "pdfjs"
    d.mkdir(parents=True, exist_ok=True)
    have = 0
    for name in PDFJS_FILES:
        fp = d / name
        if fp.exists() and fp.stat().st_size > 0:
            have += 1
            continue
        url = f"{PDFJS_CDN}/{PDFJS_VERSION}/{name}"
        try:
            r = httpx.get(url, follow_redirects=True, timeout=120.0)
            r.raise_for_status()
            fp.write_bytes(r.content)
            have += 1
            log.info("library: vendored %s (%d bytes)", name, len(r.content))
        except Exception as e:
            log.warning("library: pdf.js fetch failed for %s: %s (page will fall back to CDN)", name, e)
    return have


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    config.ensure_dirs()
    fetch()
    fetch_pdfjs()


if __name__ == "__main__":
    main()
