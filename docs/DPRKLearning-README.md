# DPRK Learning

A self-hosted mirror of KCNA (kcna.kp) news, rebuilt as a clean Korean/English
news site that will grow into a **Korean learning tool** with a Viki-style
hover-translation mode.

This is **Phase 1**: a Korean scraper + storage + a minimal viewer. See
`ARCHITECTURE.md` for the full plan and `SCRAPING_NOTES.md` for the validated
scraping spec.

> Content note: KCNA is DPRK state media. This project mirrors publicly
> available content for language study; article text and media are reproduced
> unchanged.

---

## Quick start (Docker — recommended)

```bash
docker compose up --build
```

This starts two services:
- **scraper** — fetches recent Korean articles + media right away, then
  re-checks every 60 minutes.
- **web** — the viewer at <http://localhost:8000>.

The first scrape takes a few minutes (the origin is deliberately slow and the
scraper is polite). Refresh the page as articles arrive.

Data (the SQLite DB + downloaded media) lives in `./data/`, so it persists
across restarts.

## Run without Docker

```bash
pip install -r requirements.txt

# one scraping pass (good for a first test)
python -m app.scrape --once --limit 20

# start the viewer
uvicorn app.web:app --reload
# -> http://localhost:8000
```

## Useful commands

```bash
python -m app.scrape --once              # single pass, then exit
python -m app.scrape --once --limit 10   # only fetch up to 10 new articles
python -m app.scrape --loop              # run forever on a schedule
python -m app.scrape --langs kp,en       # scrape Korean + English (Phase 3)
pytest                                    # run the parser tests
```

---

## How it works

```
kcna.kp ──httpx──▶ scraper ──▶ data/kcna.db   (article text + metadata)
                          └──▶ data/media/…    (downloaded images/video)
                                   ▲
data/  ◀──reads── web (FastAPI) ───┘  →  http://localhost:8000
```

- **No headless browser, no API keys, no accounts.** KCNA article pages are
  server-rendered, so a plain HTTP client with a browser User-Agent + a warmed
  cookie session is enough.
- **Resilient to the flaky origin:** retries with exponential backoff,
  rate-limiting, and resumable runs (each article's status lives in the DB, so a
  crash mid-run never loses or corrupts data).
- **Korean ↔ English linking is built in:** KCNA uses the *same article hash*
  across languages, so the KO/EN toggle in the viewer lights up automatically
  once English articles are scraped.

## Configuration

Copy `.env.example` to `.env` to tune languages, scrape interval, request delay,
and batch size. All have sensible defaults.

## Project layout

```
app/
  config.py        settings (env-overridable)
  db.py            SQLAlchemy models + session
  kcna_client.py   polite, retrying HTTP client
  parser.py        HTML parsing (validated selectors)
  scraper.py       discover → fetch → store → download media
  scrape.py        CLI (--once / --loop)
  web.py           FastAPI viewer
  templates/       Jinja2 pages
  static/          CSS
tests/             parser tests + fixture
```

## Reading an article

Every article is **one bilingual document** — there is no separate Korean and
English version. Each Korean sentence sits on top with its **English translation
directly below it**, alternating down the page. The **KR** and **EN** buttons at
the top toggle each language on or off (turn EN off to quiz yourself; turn KR off
to read English only). Every Korean word is boxed — hover it to see a **morpheme
breakdown** with plain-English grammar labels (particles, endings, tense) *and* a
**dictionary meaning** for content words. All analysis is offline with Kiwi — no
API keys.

The dictionary lives in `app/data/dict_ko_en.json` (curated for the KCNA/news
domain). Expand it by editing that file or dropping a `dict_ko_en.local.json`
beside it — entries are merged automatically.

## Roadmap

- **Phase 1:** Korean scraper, storage, minimal viewer. ✅
- **Phase 2:** presentation polish (newspaper styling). ✅
- **Phase 3:** scrape `/en`, pair KO↔EN by shared hash, single bilingual
  article view with KR/EN toggles, KO·EN badge on the list. ✅
- **Phase 4:** Korean morphological analysis (Kiwi), sentence-aligned English
  interleaved line-by-line, hover morpheme + dictionary glosses. ✅
- **Photos:** KCNA keeps photos in a separate section; each photo set links
  back to its article, so images are matched to the right article and shown
  inline. ✅
- **Next ideas:** larger KO→EN dictionary, saved vocab/flashcards, finer
  word-level alignment, video support, photo-section pagination.
