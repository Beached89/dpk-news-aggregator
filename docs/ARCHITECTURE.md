# DPRK Learning — Architecture & Build Plan

A self-hosted mirror of KCNA (kcna.kp) news content, rebuilt as a clean
Korean/English news site with a Viki-style Korean **learning mode**.

> **Content note:** KCNA is DPRK state media (propaganda). This project mirrors
> publicly available content for language-learning purposes — a well-established
> use (cf. NK News' "KCNA Watch"). Article text and media are preserved
> unchanged; only presentation/formatting is reworked.

---

## 1. Guiding constraints

These shape every decision below:

- **The source is JS-rendered.** A raw fetch of `kcna.kp/kp` returns only a
  frameset/"open in IE8+" shell — the real content loads client-side. A plain
  HTTP scraper will not work; we need a **browser engine** (Bright Data
  Scraping Browser, or Playwright).
- **The servers are slow and unreliable.** The scraper must assume failure is
  normal: aggressive retries, exponential backoff, capped concurrency, polite
  rate-limiting, and **resumable** runs (a crash mid-run loses nothing).
- **Content must never change.** Text and media are stored verbatim. Only the
  surrounding HTML/CSS presentation is ours.
- **Runs anywhere via Docker.** `docker compose up` is the only command needed
  to host it.

---

## 2. Stack

| Concern            | Choice                                   | Why |
|--------------------|------------------------------------------|-----|
| Language           | **Python 3.11**                          | One language across scraping, API, and Korean NLP |
| Page rendering     | **Playwright (headless Chromium)** | Pure Python, no account/key, renders the JS/frameset; official Docker image bundles the browser |
| Light-path parsing | **httpx + selectolax** (try first) | If inner frame endpoints serve real HTML, skip the browser entirely |
| HTML parsing       | **selectolax** (+ BeautifulSoup fallback) | Fast, tolerant of messy markup |
| Storage (data)     | **SQLite** via SQLAlchemy (start) → Postgres later | Zero-config now; SQLAlchemy makes the swap trivial |
| Storage (media)    | **Local filesystem**, deduped by content hash | Simple, backup-friendly |
| Web framework      | **FastAPI + Jinja2**                     | JSON API + server-rendered "traditional HTML+CSS" pages |
| Scheduler          | **APScheduler** (in-process cron)        | Periodic "check for new content" runs |
| Korean morphology  | **kiwipiepy (Kiwi)**                     | Fast, accurate, pip-only (no Java/Mecab install pain) |
| KO↔EN alignment    | **SimAlign / awesome-align**             | Word/phrase alignment via multilingual embeddings |
| Media              | **Pillow** (images), **yt-dlp + ffmpeg** (video) | Download, verify, transcode if needed |

---

## 3. Components

```
                    ┌─────────────────────────────────────────┐
                    │              docker compose               │
                    │                                           │
  kcna.kp  ◀──────▶ │  [scraper]  ──writes──▶  [ /data ]        │
  (via Bright Data) │   • discover new articles    • sqlite.db  │
                    │   • render + extract         • media/      │
                    │   • download media                        │
                    │   • schedule re-runs                      │
                    │                                  ▲         │
                    │  [web]  ──reads──────────────────┘         │
                    │   • FastAPI + Jinja2                       │
                    │   • article list / detail                 │
                    │   • KO / EN / both toggle                 │
                    │   • learning mode (hover alignment)       │
                    └─────────────────────────────────────────┘
                              ▲
                              │  http://localhost:8000
                         you + friends
```

### 3.1 Scraper
- **Discover:** crawl the KO (`/kp`) and EN (`/en`) article indexes by
  date/category; compute the set of article IDs not already in the DB.
- **Render & extract:** load each article through the browser engine; pull
  title, body paragraphs, publish date, category, and media URLs.
- **Download media:** fetch images/video to `/data/media/<article_id>/`,
  dedupe by SHA-256 hash.
- **State machine:** every item has a status (`discovered → fetched → failed`)
  in a queue table; retries with backoff; resume on restart.
- **Politeness:** capped concurrency + delay between requests so we don't hammer
  the flaky origin.

### 3.2 Data model (initial)
- `articles` — id, lang (`ko`/`en`), source_url, kcna_article_no, title,
  body_html, body_text, published_at, category, content_hash, scraped_at
- `media` — id, article_id, kind (`image`/`video`), source_url, local_path,
  sha256, width/height/duration
- `article_links` — ko_article_id, en_article_id, method, confidence
- *(Phase 4)* `sentences`, `tokens` (surface, lemma, POS, span), `alignments`
  (ko_token_span ↔ en_span)

### 3.3 KO↔EN linking (Phase 3)
Primary match on KCNA article number + date (the same story usually shares an
identifier across the `/kp` and `/en` trees); fallback to date + title/content
heuristics. Stored in `article_links`.

### 3.4 Web app
- **List view:** newest articles, by date/category.
- **Detail view:** a per-article toggle — **Korean only / English only / both
  side-by-side** — rendered server-side from stored content + local media.
- **Learning mode (Phase 4):** Korean text segmented into POS-tagged
  tokens/phrases, each boxed; hovering a Korean span highlights its aligned
  English directly below (Viki-style). Alignments are precomputed server-side;
  the frontend just renders spans + lightweight hover JS.

### 3.5 Docker
`docker-compose.yml` with `web` (uvicorn) and `scraper` (scheduler) services
sharing a `/data` volume; `.env` holds `BRIGHTDATA_API_KEY`. Postgres added as a
service only if/when we outgrow SQLite. One `docker compose up` runs everything.

---

## 4. Phased build plan

| Phase | Deliverable | Key pieces |
|-------|-------------|-----------|
| **P1 — Korean scraper + mirror** | Scheduled scraper that detects new KO articles, stores text + media, resumable | browser render, discovery, state machine, SQLite, media store, APScheduler |
| **P2 — Clean site** | HTML/CSS site rendering mirrored KO articles + media | FastAPI, Jinja2, list/detail views, local media serving |
| **P3 — English + linking** | EN scraping, KO↔EN pairing, KO/EN/both toggle | `/en` discovery, `article_links`, toggle UI |
| **P4 — Learning mode** | POS-segmented Korean with hover-aligned English | Kiwi morphology, KO↔EN word/phrase alignment, learning-mode UI |

---

## 5. What runs where (the "walk away" model)

No external accounts or API keys. Everything is pure Python + Playwright.

- **I build and unit-test in my sandbox**, writing all project files into this
  folder (`DPRKLearning`) and validating the extraction logic against **saved
  sample pages** (my sandbox can't download chromium or reach kcna.kp directly).
- **The scraper runs on your machine**, where chromium and the live site are
  reachable. The Playwright Docker image bundles the browser, so:
- **You run one command** to scrape live and host it for friends:
  `docker compose up`. (I'm blocked from typing into your terminal, so this last
  launch step stays yours.)

### To pin exact selectors (pick one)
1. **Claude in Chrome** — if the browser extension is connected, I open a live
   KCNA article in your browser, read the rendered DOM, and derive precise
   selectors. *(Recommended — fastest, uses real structure.)*
2. **Save sample pages** — you save one KO article + its `/en` counterpart as
   HTML into this folder; I build against those fixtures.
3. **Defensive extractor** — I write a robust generic extractor, you run it once,
   and we refine from the output.

---

## 6. Risks & mitigations

- **Source downtime/slowness** → browser rendering + retries/backoff + resumable
  queue + caching; a failed run never corrupts the mirror.
- **Site structure changes** → selectors isolated in one module; extraction
  covered by tests against saved fixtures.
- **Media size** → hash-dedupe; optional video transcode; lazy download.
- **Alignment quality (P4)** → start with sentence alignment + statistical word
  alignment; treat as "best effort, reviewable," not ground truth.
