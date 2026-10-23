# DPRK News Aggregator

A DPRK-only news reader: KCNA / Rodong / VOK / magazine ingestion plus English
coverage from 38 North and Daily NK (full-text RSS), Korean → English
self-translation, word-level alignment for language learning, and an agriculture
quantity analysis. The front end is a static page; **nothing of this runs on your
computer or on a server you pay for.**

Live site: <https://beached89.github.io/dpk-news-aggregator/> — published by the
pipeline on every successful run, with no cloud credentials configured.

## How it runs

```
GitHub Actions (cron, free)                    where it lands
┌───────────────────────────────────┐       ┌────────────────────────────────┐
│ 1. pull state   kcna.db           │──R2──▶│ state: kcna.db                 │
│                 translations.json │       │        translations.json       │
│ 2. python -m app.scrape --once    │       └────────────────────────────────┘
│      scrape + translate + analyse │
│ 3. python -m app.publish --out site   site/ = index.html + assets/ + data/
│ 4. stage data/media into site/    │──────▶ GitHub Pages   (no secrets at all)
│ 5. optional wrangler deploy       │──────▶ Cloudflare Pages (own domain)
└───────────────────────────────────┘
```

Why this shape, and not one long-lived app:

* **Cloudflare Workers / Pages Functions cannot run this code.** They are V8
  isolates: no CPython, so no `selectolax`, `kiwipiepy`, `torch` or `PyMuPDF`, and
  the free tier caps an invocation at well under the time it takes to fetch a
  dozen slow DPRK pages. The ingestion half therefore cannot be moved into the edge.
* **Pages is static-only**, so the FastAPI app (`app/web.py`) is not what the public
  sees. Each run writes the same information out as JSON (`app/publish.py`) and the
  browser renders it (`index.html` + `assets/app.js`, no framework, no build step).
* **Scheduled GitHub Actions is the only free general-purpose computer available
  here**, so it is the scheduler, the scraper and the translator. Public repositories
  get unlimited Actions minutes; the state that normally lives on a disk is kept in
  Cloudflare R2 (10 GB free, no egress charges) and re-attached each run. Skipping R2
  entirely still works — every run then starts from an empty database.
* Git-integration *builds* are deliberately **not** used on either host: GitHub Pages is
  configured as "Deploy from a workflow" and Cloudflare as direct upload, because a build
  run from the repository root would publish `app/*.py` to the public internet.
  `app/publish.py` copies in only `index.html` and `assets/`; the workflow stages
  `data/media/` into `site/media/` so the photos the JSON refers to are actually deployed.

## Layout

| Path | Purpose |
|---|---|
| `index.html`, `assets/app.js` | Static front end: fetches `data/*.json` and renders it (no build step, no framework) |
| `app/scrape.py`, `app/scraper.py` | CLI and ingestion for kcna / rodong / vok / magazine / agriculture / 38north / dailynk |
| `app/rssfeed.py` | Full-text RSS ingestion for the English sources (38 North, Daily NK): parsing, image and byline extraction, category trimming |
| `app/translate.py` | KO→EN client: batched keyless Google endpoint, DeepL, Google Cloud, LibreTranslate — plus `translate_to_ko()` for the English-native sources |
| `app/tcache.py` | Persistent translation cache — the reason the free tiers are enough |
| `app/publish.py` | SQLite → static JSON export (**stdlib only**, no heavy imports) |
| `app/analysis.py`, `app/align.py`, `app/agri_*` | Morphology, KO↔EN alignment, agriculture analysis |
| `app/web.py` | FastAPI viewer for local use only (not deployed) |
| `app/data/dict_ko_en.json` | Bundled KO→EN glossary used by the fallback translator |
| `.github/workflows/scrape.yml` | The scheduled pipeline described above |
| `.github/workflows/probe.yml` | Manual: can a runner reach the origins at all? |
| `.github/workflows/ci.yml` | Tests + publish smoke test on every push |
| `requirements-scrape.txt` | Dependency set used by CI — **includes `kiwipiepy`**; still no torch / whisper / PyMuPDF |
| `requirements.txt` | Full local set, including the heavyweight analysis extras |
| `tests/` | `pytest` suite; `tests/test_translate.py` runs offline |
| `legacy-docker/`, `docs/`, `PROJECT_CONTEXT.md` | Retired container setup and older notes |

`site/` (generated export), `data/` (SQLite + media) and `.env` are git-ignored.
`/data/` is anchored to the repository root on purpose: an unanchored `data/`
pattern would also match `app/data/` and silently drop the bundled glossary.

## Setting the pipeline up (once)

1. **Run `Origin reachability probe` first** (Actions → *Origin reachability probe* →
   Run workflow). It prints the HTTP status GitHub's runner gets from KCNA, Rodong,
   VOK and the translation endpoint. If those come back `000`/`403`, the origins are
   refusing datacenter traffic and ingestion has to run from somewhere else — no
   amount of pipeline changes will fix that, so check before anything else.
   (Already verified on 2026-10-23: all four origins answered — see *Known issues*.)
**Option A — GitHub Pages: nothing to configure.** Pages is enabled on this repository
with *Deploy from a workflow*, and the `pages` job at the end of `scrape.yml` publishes
`site/` after every successful pipeline. Skip steps 2–4 and go to step 5.

**Option B — Cloudflare Pages: only if you want your own domain.** This is the path that
needs secrets and a bucket.

2. **Create a Cloudflare Pages project** with *Build advanced* → **Production
   type: Direct upload**, no build command, project name e.g. `dpk-news`. Do not
   connect the repository for auto-builds (see the note in *How it runs*).
3. **Create a private R2 bucket** (e.g. `dpk-news`) for `state/kcna.db` and
   `state/translations.json`. Optional: a public R2 dev bucket for mirrored images,
   then set `MEDIA_BASE` to its URL and `MIRROR_MEDIA=1`.
4. **Add the repository configuration** (Settings → Secrets and variables → Actions):

   | Name | Kind | What |
   |---|---|---|
   | `CLOUDFLARE_API_TOKEN` | secret | Pages: edit deploy (account token) |
   | `CLOUDFLARE_ACCOUNT_ID` | secret | Cloudflare account id |
   | `CLOUDFLARE_PAGES_PROJECT` | secret | Pages project name from step 2 |
   | `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | secret | R2 S3 token, bucket read/write |
   | `TRANSLATE_API_KEY` | secret | only for `deepl` / `google` providers |
   | `R2_ENDPOINT` | variable | `https://<account>.r2.cloudflarestorage.com` |
   | `R2_BUCKET` | variable | bucket name |
   | `TRANSLATE_PROVIDER` | variable | `google_free` (default) / `deepl` / `google` / `libretranslate` |
   | `SCRAPE_KCNA`, `SCRAPE_RODONG`, `SCRAPE_VOK`, `SCRAPE_MAGAZINES` | variable | `1`/`0` per source. Both KCNA and Rodong are live; delete a variable to fall back to the config default. |
   | `SCRAPE_38NORTH`, `SCRAPE_DAILYNK` | variable | `1`/`0` for the English RSS sources. Both default **on**; they need no secrets and cost one request per source. |
   | `RSS_MIRROR_FULLTEXT`, `RSS_TRANSLATE_KO`, `RSS_MAX_IMAGES`, `RSS_MAX_CATEGORIES` | variable | Full article body vs. the publisher's own summary (default: full); generate the Korean side of English stories (default: off); per-item photo and tag caps. |
   | `MAX_AGE_HOURS`, `REQUEST_DELAY`, `MAX_ARTICLES_PER_RUN` | variable | pacing and window |
   | `MEDIA_BASE`, `MIRROR_MEDIA` | variable | optional image mirroring. Both empty (the default) is correct for GitHub Pages: media is staged into `site/media/` and referenced by a relative path. Pointing `MEDIA_BASE` at an `http://` R2 URL reintroduces mixed content on an `https` site. |

   Already set on the repository (2026-10-23): `SCRAPE_RODONG=1`,
   `MAX_AGE_HOURS=120`, `REQUEST_DELAY=2.0`, `MAX_ARTICLES_PER_RUN=60`,
   `TRANSLATE_PROVIDER=google_free`, `TRANSLATE_TARGET=EN-US`. The old
   `SCRAPE_KCNA=0` variable has been deleted, so KCNA runs again with the rebuilt
   parser. Pages itself is enabled
   (`gh api repos/<owner>/<repo>/pages` reports `build_type: workflow`,
   `https://<user>.github.io/<repo>/`), which is why the site goes public without any
   secret. `gh secret list` is still empty — that only skips the optional Cloudflare job.
   Check either with `gh variable list` / `gh secret list`.

5. **Run `Scrape, translate, publish`** manually once, then leave the schedule alone.
   It runs every 6 hours. On `main` the `pages` job then deploys the result; on a branch
   it stops at the `site` artifact. Until R2 is configured each run starts from an empty
   database, so the site shows the last 120 hours (`MAX_AGE_HOURS`) of whatever the run
   managed to fetch.

## Translation

`translate_texts()` is cache-first, then de-duplicates, then sends **batches of 45
sentences per request** to the configured provider, and keeps order 1:1. Measured
against the live keyless endpoint: 4 new sentences = 1 request (1.24 s); replaying
800 already-seen sentences = 0 requests (0.001 s).

Ranking for this project, all free:

| Provider | Setup | Reality |
|---|---|---|
| `google_free` (default) | none | Unofficial endpoint, no key. Fast and batched, but Google may rate-limit or change it without notice — fine for a mirror, not a contract. |
| `deepl` | free key | 500k characters/month, official and stable. **Recommended once the site is public**; with the cache, steady-state usage stays far inside the free tier. |
| `google` | key + billing | Most reliable Korean quality, paid beyond credits. |
| `libretranslate` | your own instance | The bundled container is retired with the Docker setup; only useful if you host one. |

Everything the provider cannot translate comes back as an empty string so the site
can say *untranslated*. It used to fall back to returning the Korean text, which a
learner reads as English; `TRANSLATE_KEEP_SOURCE_ON_FAIL=1` restores that.

## Data contract for the front end

| File | Contents |
|---|---|
| `/data/manifest.json` | `generated_at`, `schema`, counts, `sources`, `media_local` |
| `/data/articles.json` | the card list, newest-first: `id`, `date`, `sources`, `title_ko`, `title_en`, `dateline`, `categories`, `cover`, `videos`, `snippet`, `has_translation`, `has_alignment`, `group` — deliberately compact, **no url and no body text** |
| `/data/a/<hash>.json` | one story: `url`, full `body_ko`, `body_en`, parsed `align` (KO/EN per sentence + word pairs), `media`, `subtitle`, `dedup_key` |
| `/data/categories.json` | nav with per-category counts, hidden categories removed |
| `/data/agri.json` | agriculture facts, place observations, commodity and end-use tables |
| `/media/<hash>/<file>` | mirrored images, staged by the workflow from `data/media` (the export only references them) |

`assets/app.js` therefore makes two kinds of request: one `articles.json` for the list,
then `a/<hash>.json` per story it opens. Because `articles.json` carries no `url`, the
reader merges the detail record over the card before it renders the "open the original"
link — that merge is load-bearing, not decoration.

A `hash` is one article (Korean on the `kp` row, English on the `en` row of the same
hash); `group` lists the hashes of the same report from different sources, so the
client can collapse duplicates the way `app/web.py` does.

## Local development

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-scrape.txt pytest    # add requirements.txt for alignment/video/PDF
copy .env.example .env
pytest tests/test_translate.py tests/test_parser.py
python -m app.scrape --once --limit 10 --source rodong
python -m app.publish --out site && python -m http.server -d site
```

`DATA_DIR` selects where the SQLite database and media live; it defaults to `./data`.
`python -m app.publish --help` lists the export options (`--max-age-hours`,
`--limit`, `--media-base`, `--no-static`).

Serve the directory rather than opening the file: the page reads its data over HTTP, so
`index.html` opened straight from disk shows the *Could not load the export* notice.
`python -m http.server -d site` and visit `http://localhost:8000`. To preview media the
way the workflow does, copy `data/media` to `site/media` first.

## Known issues and caveats

* **KCNA was rebuilt in 2026 — the scraper now speaks the new site.** Routes moved off
  `.kcmsf`: articles are `/{lang}/article/detail/<32 hex>`, categories
  `/article/list/<32 hex>`, photo galleries `/gallery/detail/<32 hex>`, video
  `/video/detail/<32 hex>` with the files at `/video/stream/<hex>`, and every image is
  `/photo/<hex>`. Two quirks of the rebuild are load-bearing in the code: an article page
  prints **no publication year** (the dateline is "(평양 10월 20일발 조선중앙통신)"), so the
  year comes from the `[2026.10.20.]` stamp beside the link on the homepage or listing and
  is passed to `parse_article(..., listing_date=...)`; and `/photo/…` answers **200 with an
  empty body** unless the request carries a kcna.kp `Referer`, so photo downloads always
  send the article URL as referer. Listing pages paginate through a CSRF-protected `POST`,
  which is not implemented — the first page of each category plus the homepage already
  covers a rolling 5-day window.
* **Zero stories used to be a hidden dependency bug, not the topic filter.** A full-limit
  run went green while exporting an empty site. Reading the log rather than the badge:
  10 Rodong articles had cleared the topic and photo filters and were then killed by
  `No module named 'kiwipiepy'` — the sentence splitter lived only in Kiwi, and
  `requirements-scrape.txt` excluded Kiwi as a heavy optional extra while the scrape path
  treated it as mandatory. Held articles stay hidden and are retried forever, so nothing
  looked wrong. Fixed by installing `kiwipiepy` in the CI set and adding a regex fallback
  (`app/analysis.py:split_korean_sentences`); the identical run then exported **10 stories,
  10 translated, 134,054 bytes, `with_photo: 10`**. When a run looks empty, read the
  `run complete (...)` tally and grep for `held:` — `off-topic, skipped` and
  `no image, skipped` are `TOPIC_FILTER` working as designed, but `held:` lines are failures.
* **The published page used to be a mock; it now reads the export.** The old `index.html`
  shipped designed-but-fixed headlines (some invented, e.g. "Source: 38 North") with
  `href="#"` throughout while `data/*.json` sat underneath unused, because `app/web.py`
  renders the same data server-side and hid the gap in development. `assets/app.js` is now
  that client: story list, per-story reader, English / 조선말 / 한-EN-together modes (with a
  stacked fallback when the two languages do not break into the same number of paragraphs),
  category tabs built from `categories.json`, and relative URLs throughout so the same
  directory works at a domain root or under `/dpk-news-aggregator/`. Scraped text reaches the
  DOM only through `textContent` — it is never re-parsed as markup.
* **Photos needed a step nobody had written.** The export references
  `media/<hash>/00_img.jpg`, but the scraper stores those bytes in `data/media` and nothing
  copied them into `site/`, so every image 404'd while `manifest.json` still reported
  `with_photo: 10`. The workflow now stages `site/media/` before uploading, and `MEDIA_BASE`
  defaults to the relative `media` — the previous `/media` default would have resolved above
  a project Pages site even with the files present. A missing image degrades to an
  "image unavailable" box instead of a broken icon.
* **The page keeps its looks from a CDN.** `index.html` pulls Tailwind and Noto Serif from
  `cdn.tailwindcss.com` / Google Fonts, the way the mock did. The data path does not need
  them — rendering was verified with those hosts blocked — but without access to them the
  site is unstyled text. Vendoring them locally is the first thing to do if that matters.
* **Existing English rows can hold Korean titles.** Exporting the current database
  showed 15 of 19 `lang='en'` rows whose title is identical to the Korean — the
  fallback bug described above, recorded before the fix. Those articles need one more
  translation pass (re-run the scraper while they are inside `MAX_AGE_HOURS`, or
  re-ingest them) to get real English.
* Runner reachability **is verified**: from `ubuntu-latest` (Azure `westus3`/`centralus`)
  a probe run got `200` from `kcna.kp`, `vok.rep.kp` and `korean-books.com.kp`, `302` from
  `rodong.rep.kp`, and a real batched keyless translation came back
  (`삼리폭 두면에서` -> `On two sides of Samripok`). Datacenter IPs are not blocked.
* GitHub Actions cron for free accounts is best-effort and can start late; use
  `workflow_dispatch` when you want it now.
* The keyless endpoint is not a sanctioned API and DeepL/Google terms restrict some
  uses of translated output; check them before republishing translations anywhere.
* `align` (torch), `transcribe` (faster-whisper + ffmpeg) and `magazines` (PyMuPDF)
  are intentionally outside the CI install, so those features only work locally with
  the full `requirements.txt`.

