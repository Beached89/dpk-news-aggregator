# DPRK News Aggregator

A DPRK-only news reader: KCNA / Rodong / VOK / magazine ingestion, Korean → English
self-translation, word-level alignment for language learning, and an agriculture
quantity analysis. The front end is a static page; **nothing of this runs on your
computer or on a server you pay for.**

## How it runs

```
GitHub Actions (cron, free)                 Cloudflare (free)
┌───────────────────────────────────┐       ┌──────────────────────────┐
│ 1. pull state   kcna.db           │──R2──▶│  state: kcna.db          │
│                 translations.json │       │          translations.json│
│ 2. python -m app.scrape --once    │       └──────────────────────────┘
│      scrape + translate + analyse │
│ 3. python -m app.publish --out site       index.html + data/*.json  ──▶ Pages
│ 4. deploy site/ via wrangler      │           (public site)
└───────────────────────────────────┘
```

Why this shape, and not one long-lived app:

* **Cloudflare Workers / Pages Functions cannot run this code.** They are V8
  isolates: no CPython, so no `selectolax`, `kiwipiepy`, `torch` or `PyMuPDF`, and
  the free tier caps an invocation at well under the time it takes to fetch a
  dozen slow DPRK pages. The ingestion half therefore cannot be moved into the edge.
* **Pages is static-only**, so the FastAPI app (`app/web.py`) is not what the public
  sees. Each run writes the same information out as JSON (`app/publish.py`) and the
  browser renders it.
* **Scheduled GitHub Actions is the only free general-purpose computer available
  here**, so it is the scheduler, the scraper and the translator. Public repositories
  get unlimited Actions minutes; the state that normally lives on a disk is kept in
  Cloudflare R2 (10 GB free, no egress charges) and re-attached each run.
* Pages git-integration builds are deliberately **not** used: the build output has to
  be `site/`, and building from the repository root would publish `app/*.py` to the
  public internet. `app/publish.py` copies in only `index.html` and `assets/`.

## Layout

| Path | Purpose |
|---|---|
| `index.html`, `assets/` | Cloudflare Pages front end (no build step) |
| `app/scrape.py`, `app/scraper.py` | CLI and ingestion for kcna / rodong / vok / magazine / agriculture |
| `app/translate.py` | KO→EN client: batched keyless Google endpoint, DeepL, Google Cloud, LibreTranslate |
| `app/tcache.py` | Persistent translation cache — the reason the free tiers are enough |
| `app/publish.py` | SQLite → static JSON export (**stdlib only**, no heavy imports) |
| `app/analysis.py`, `app/align.py`, `app/agri_*` | Morphology, KO↔EN alignment, agriculture analysis |
| `app/web.py` | FastAPI viewer for local use only (not deployed) |
| `app/data/dict_ko_en.json` | Bundled KO→EN glossary used by the fallback translator |
| `.github/workflows/scrape.yml` | The scheduled pipeline described above |
| `.github/workflows/probe.yml` | Manual: can a runner reach the origins at all? |
| `.github/workflows/ci.yml` | Tests + publish smoke test on every push |
| `requirements-scrape.txt` | Light dependency set used by CI (no torch / kiwipiepy / whisper) |
| `requirements.txt` | Full local set, including the heavyweight analysis extras |
| `tests/` | `pytest` suite; `tests/test_translate.py` runs offline |
| `legacy-docker/`, `docs/`, `PROJECT_CONTEXT.md` | Retired container setup and older notes |

`site/` (generated export), `data/` (SQLite + media) and `.env` are git-ignored.
`site/` (generated export), `data/` (SQLite + media) and `.env` are git-ignored.
`/data/` is anchored to the repository root on purpose: an unanchored `data/`
pattern would also match `app/data/` and silently drop the bundled glossary.

## Setting the pipeline up (once)

1. **Run `Origin reachability probe` first** (Actions → *Origin reachability probe* →
   Run workflow). It prints the HTTP status GitHub's runner gets from KCNA, Rodong,
   VOK and the translation endpoint. If those come back `000`/`403`, the origins are
   refusing datacenter traffic and ingestion has to run from somewhere else — no
   amount of pipeline changes will fix that, so check before anything else.
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
   | `SCRAPE_KCNA`, `SCRAPE_RODONG`, `SCRAPE_VOK`, `SCRAPE_MAGAZINES` | variable | `1`/`0` per source |
   | `MAX_AGE_HOURS`, `REQUEST_DELAY`, `MAX_ARTICLES_PER_RUN` | variable | pacing and window |
   | `MEDIA_BASE`, `MIRROR_MEDIA` | variable | optional image mirroring |

5. **Run `Scrape, translate, publish`** manually once, then leave the schedule alone.
   It runs every 6 hours. Until R2 is configured it still works, but each run starts
   from an empty database and the site artifact is only in the Actions run.

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
| `/data/articles.json` | stories newest-first: titles KO/EN, date, categories, `cover`, `snippet`, `has_translation`, `group` |
| `/data/a/<hash>.json` | one story: full `body_ko`, `body_en`, parsed `align` (KO/EN per sentence + word pairs), media |
| `/data/categories.json` | nav with per-category counts, hidden categories removed |
| `/data/agri.json` | agriculture facts, place observations, commodity and end-use tables |

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

## Known issues and caveats

* **Existing English rows can hold Korean titles.** Exporting the current database
  showed 15 of 19 `lang='en'` rows whose title is identical to the Korean — the
  fallback bug described above, recorded before the fix. Those articles need one more
  translation pass (re-run the scraper while they are inside `MAX_AGE_HOURS`, or
  re-ingest them) to get real English.
* Runner reachability for the DPRK origins is **not yet verified**; run `probe.yml`.
* GitHub Actions cron for free accounts is best-effort and can start late; use
  `workflow_dispatch` when you want it now.
* The keyless endpoint is not a sanctioned API and DeepL/Google terms restrict some
  uses of translated output; check them before republishing translations anywhere.
* `align` (torch), `transcribe` (faster-whisper + ffmpeg) and `magazines` (PyMuPDF)
  are intentionally outside the CI install, so those features only work locally with
  the full `requirements.txt`.

