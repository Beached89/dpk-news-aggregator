"""Generic cover art for stories the source never paired with a photo.

Plenty of mirrored stories have no image at all — every 38 North / Daily NK item
arrives with `cover: null` unless the feed happened to carry one. Rather than
render "no photo in source" on a third of the front page, the export gives those
cards a generic image from the pool in `assets/placeholders/`.

Two rules shape this module:

* **Nothing is fetched while serving.** The pool is committed to the repo, and
  `publish.py::_copy_static()` copies all of `assets/` into `site/`, so Pages
  serves it with no runtime fetch and no extra workflow step. Refresh the pool
  explicitly, locally: `python -m app.placeholders`.
* **The pick is stable, not per-request-random.** It is keyed on the story id, so
  a card keeps the same art from one 6-hourly run to the next instead of
  reshuffling on every deploy (and every reload, since the JSON is static).
  Set `PLACEHOLDER_SALT` to re-roll every assignment at once.

Stdlib only, like publish.py: the export step must run on a bare interpreter.
URLs are relative (`assets/placeholders/ph-01.jpg`) for the same reason media
paths are — a project Pages site lives under `/<repo>/`, so an absolute path
would resolve outside the site. `app.js::assetUrl()` resolves them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config

log = logging.getLogger("kcna.placeholders")

APP_DIR = Path(__file__).parent
POOL_DIR = APP_DIR.parent / "assets" / "placeholders"
URL_PREFIX = "assets/placeholders"
MANIFEST_NAME = "manifest.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Lorem Picsum: a curated mirror of freely usable stock photography. No account,
# no key, no attribution required — see https://picsum.photos/license.
LIST_API = "https://picsum.photos/v2/list?page={page}&limit=100"
IMG_API = "https://picsum.photos/id/{pid}/{width}/{height}"
MAX_PER_AUTHOR = 2          # variety guard: never let one photographer dominate
MIN_BYTES = 4096            # anything smaller is an error page, not a photo
LICENCE_NOTE = ("Lorem Picsum — free to use, no attribution required "
                "(https://picsum.photos/license)")


# ------------------------------------------------------------------ selection
_cache: tuple[tuple[str, ...], float] | None = None


def pool_files() -> list[str]:
    """Names of the pool images, sorted; cached and re-read when the folder
    changes (its mtime moves whenever a file is added or removed)."""
    global _cache
    try:
        stamp = POOL_DIR.stat().st_mtime
    except OSError:
        _cache = None
        return []
    if _cache is not None and _cache[1] == stamp:
        return list(_cache[0])
    names = tuple(sorted(p.name for p in POOL_DIR.iterdir()
                         if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES))
    _cache = (names, stamp)
    return list(names)


def pool_size() -> int:
    return len(pool_files())


def _url(name: str) -> str:
    return f"{URL_PREFIX}/{name}"


def pick(key: str | None, salt: str = "") -> str | None:
    """A placeholder path for `key` (the story id), or None when the pool is
    empty. Deterministic: the same key always lands on the same image, and the
    keys spread evenly over the pool."""
    names = pool_files()
    if not names:
        return None
    if not key:
        return _url(random.choice(names))
    digest = hashlib.blake2b(f"{salt}|{key}".encode("utf-8"), digest_size=8).digest()
    return _url(names[int.from_bytes(digest, "big") % len(names)])


def pick_random() -> str | None:
    """An unpredictable pick — for keyless one-off contexts."""
    names = pool_files()
    return _url(random.choice(names)) if names else None


# -------------------------------------------------------------------- builder
def _get(url: str, *, timeout: float = 45.0, tries: int = 4, delay: float = 2.0) -> bytes:
    """GET with a browser UA and backoff; Picsum sits behind a CDN and stumbles."""
    last: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:                      # noqa: BLE001 — retry anything
            last = e
            if attempt < tries:
                time.sleep(delay * attempt)
    raise RuntimeError(f"{url}: {last}")


def _looks_like_image(data: bytes) -> bool:
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:4] == b"GIF8" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def _read_manifest(dest: Path) -> dict:
    try:
        data = json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_manifest(dest: Path, manifest: dict) -> None:
    (dest / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n",
                                      encoding="utf-8")


def _download(pid, width: int, height: int, grayscale: bool) -> tuple[bytes, str]:
    url = IMG_API.format(pid=pid, width=width, height=height)
    if grayscale:
        url += "?grayscale"
    data = _get(url)
    if len(data) < MIN_BYTES or not _looks_like_image(data):
        raise RuntimeError(f"picsum {pid} is not a usable image ({len(data)} bytes)")
    return data, url


def _balance(photos: list[dict]) -> list[dict]:
    """Keep at most MAX_PER_AUTHOR photos per photographer, in list order."""
    ordered: list[dict] = []
    seen_authors: dict[str, int] = {}
    for p in photos:
        author = p.get("author") or "?"
        if seen_authors.get(author, 0) < MAX_PER_AUTHOR:
            ordered.append(p)
            seen_authors[author] = seen_authors.get(author, 0) + 1
    return ordered


def _candidates(count: int, pages: int = 10, exclude_ids: set[str] | None = None,
                exclude_authors: set[str] | None = None) -> list[dict]:
    """Photos to pull, spread over as many authors as possible.

    Lorem Picsum lists are grouped by photographer, so a naive first-N would hand
    back one person's series; the per-author cap keeps the pool generic.
    `exclude_ids` / `exclude_authors` skip what the pool already holds, so a
    replace pass lands on something visibly different rather than a near twin."""
    skip_ids = exclude_ids or set()
    skip_authors = exclude_authors or set()
    photos: list[dict] = []
    for page in range(1, pages + 1):
        try:
            payload = json.loads(_get(LIST_API.format(page=page)).decode("utf-8"))
        except Exception as e:
            log.warning("list page %d failed: %s", page, e)
            continue
        if not isinstance(payload, list) or not payload:
            break
        photos += [p for p in payload if p.get("id")
                   and str(p["id"]) not in skip_ids
                   and (p.get("author") or "?") not in skip_authors]
        if len(_balance(photos)) >= count:
            break                          # enough usable photos, stop paging
    return _balance(photos)[:count]


def _entry(name: str, pid, author, url: str, data: bytes) -> dict:
    return {"file": name, "picsum_id": str(pid), "picsum_author": author,
            "source_url": url, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def build_pool(count: int = 50, width: int = 800, height: int = 450,
               grayscale: bool = False, force: bool = False,
               delay: float | None = None) -> int:
    """Scrape `count` generic images into the pool + write the manifest.

    Idempotent: existing files are kept unless `force`, and a kept file keeps the
    provenance of the pick that produced it rather than the new candidate's."""
    dest = POOL_DIR
    dest.mkdir(parents=True, exist_ok=True)
    pause = config.REQUEST_DELAY if delay is None else delay
    existing = _read_manifest(dest)
    retired = {str(x) for x in existing.get("retired_ids") or []}

    photos = _candidates(count, exclude_ids=retired)
    if not photos:
        raise RuntimeError("no candidate photos (picsum.photos unreachable?)")
    log.info("placeholder pool: %d candidate photo(s) for %d slot(s)%s",
             len(photos), count,
             f" ({len(retired)} retired by review)" if retired else "")

    known = {e.get("file"): e for e in existing.get("images", [])}
    entries: list[dict] = []
    pad = max(2, len(str(count)))
    for i, p in enumerate(photos, 1):
        name = f"ph-{i:0{pad}d}.jpg"
        target = dest / name
        prior = known.get(name) or {}
        pid, author = str(p["id"]), p.get("author")
        if target.exists() and not force:
            log.info("%s exists — keeping", name)
            data = target.read_bytes()
            pid = prior.get("picsum_id") or pid
            author = prior.get("picsum_author") or author
            url = prior.get("source_url") or \
                IMG_API.format(pid=pid, width=width, height=height)
        else:
            try:
                data, url = _download(pid, width, height, grayscale)
            except Exception as e:
                log.warning("slot %d (picsum %s) failed: %s", i, pid, e)
                continue
            target.write_bytes(data)
            time.sleep(pause)
        entries.append(_entry(name, pid, author, url, data))
        log.info("saved %s (%d bytes, picsum id %s)", name, len(data), pid)

    _write_manifest(dest, {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "https://picsum.photos", "licence": LICENCE_NOTE,
        "size": [width, height], "grayscale": grayscale,
        "retired_ids": existing.get("retired_ids") or [],
        "images": entries,
    })
    log.info("placeholder pool ready: %d image(s) in %s", len(entries), dest)
    return len(entries)


def replace_slots(slots: list[int], ids: list[str] | None = None,
                  width: int = 800, height: int = 450, grayscale: bool = False,
                  delay: float | None = None) -> int:
    """Re-scrape individual pool slots, for the review pass.

    Download the pool, look at it, and swap the few that are not generic enough
    to sit under a headline (a face, readable text, branding). Slots are 1-based —
    `ph-11.jpg` is slot 11. New photos come from `ids` when supplied, otherwise
    from candidates neither in the pool nor retired by an earlier review."""
    dest = POOL_DIR
    manifest = _read_manifest(dest)
    entries: list[dict] = manifest.get("images") or []
    if not entries:
        raise RuntimeError(f"no manifest in {dest} — run a full build first")
    pause = config.REQUEST_DELAY if delay is None else delay
    retired = {str(x) for x in manifest.get("retired_ids") or []}

    fresh: list[dict] = []
    if not ids:
        used = {str(e.get("picsum_id")) for e in entries if e.get("picsum_id")}
        authors = {e.get("picsum_author") for e in entries if e.get("picsum_author")}
        # Prefer photographers the pool does not carry at all: consecutive Picsum
        # ids are one person's series, so a new id by the same author tends to be
        # a near twin of a photo already in there.
        fresh = _candidates(len(slots), exclude_ids=used | retired,
                            exclude_authors=authors)
        if len(fresh) < len(slots):
            fresh = _candidates(len(slots), exclude_ids=used | retired)
        if len(fresh) < len(slots):
            raise RuntimeError("could not find enough fresh candidate photos")

    replaced = 0
    for n, slot in enumerate(slots, 1):
        if not 1 <= slot <= len(entries):
            log.warning("slot %d out of range (pool holds %d) — skipped",
                        slot, len(entries))
            continue
        pid = ids[n - 1] if ids and n <= len(ids) else None
        photo = {"id": pid, "author": None} if ids else fresh[n - 1]
        if not photo.get("id"):
            log.warning("slot %d: no id supplied — skipped", slot)
            continue
        name = entries[slot - 1].get("file") or f"ph-{slot:02d}.jpg"
        dropped = entries[slot - 1].get("picsum_id")
        try:
            data, url = _download(photo["id"], width, height, grayscale)
        except Exception as e:
            log.warning("slot %d (picsum %s) failed: %s", slot, photo["id"], e)
            continue
        (dest / name).write_bytes(data)
        entries[slot - 1] = _entry(name, photo["id"], photo.get("author"), url, data)
        if dropped:                        # never offer a rejected photo again
            retired.add(str(dropped))
        replaced += 1
        log.info("replaced slot %d -> %s (picsum id %s, %d bytes)",
                 slot, name, photo["id"], len(data))
        time.sleep(pause)

    manifest["images"] = entries
    manifest["retired_ids"] = sorted(retired, key=lambda x: (len(x), x))
    manifest["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_manifest(dest, manifest)
    return replaced


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        prog="python -m app.placeholders",
        description="Build the generic cover art for stories the source has no "
                    "photo for (committed to assets/placeholders).")
    ap.add_argument("--count", type=int, default=50, help="pool size (default 50)")
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--height", type=int, default=450)
    ap.add_argument("--grayscale", action="store_true",
                    help="ask Picsum for grey versions (the site already washes "
                         "colour out in CSS; colour is kept by default)")
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument("--delay", type=float, default=None,
                    help="seconds between downloads (default REQUEST_DELAY)")
    ap.add_argument("--replace-slots", default=None, metavar="2,11,41",
                    help="re-scrape only these 1-based pool slots")
    ap.add_argument("--ids", default=None, metavar="1015,1043",
                    help="with --replace-slots: explicit Lorem Picsum ids to use")
    a = ap.parse_args(argv)
    if a.replace_slots:
        slots = [int(x) for x in a.replace_slots.split(",") if x.strip()]
        ids = [x.strip() for x in a.ids.split(",") if x.strip()] if a.ids else None
        n = replace_slots(slots, ids=ids, width=a.width, height=a.height,
                          grayscale=a.grayscale, delay=a.delay)
    else:
        n = build_pool(count=a.count, width=a.width, height=a.height,
                       grayscale=a.grayscale, force=a.force, delay=a.delay)
    print(f"pool now holds {pool_size()} image(s) at {POOL_DIR}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
