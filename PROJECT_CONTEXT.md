# Kim Jong Lingo — Project Context

Orientation for a fresh session. The **README is authoritative** for how the pipeline
works; [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) is for "why does the feed
look stale?". This file only fixes the facts that keep getting mis-stated.

## 🎯 What this is

A DPRK-only news reader with a language-learning slant: KCNA / Rodong Sinmun / VOK /
magazine ingestion plus English coverage from 38 North and Daily NK, Korean → English
translation, KO↔EN sentence and word alignment, and an agriculture-quantity analysis.

It is **one project**, previously developed under three names — *DPRKLearning* (the
Docker-era app), *Kim Jong Lingo* (the site's brand) and *dpk-news-aggregator* (the
repository). Those are the same product at different stages, not three codebases.

## 📁 Where everything lives

| Item | Location |
|---|---|
| **Canonical repo and working copy** | `~/Desktop/dpk-news-aggregator/` |
| **GitHub repo** | `https://github.com/Beached89/dpk-news-aggregator` (branch `main`) |
| **Live site** | `https://beached89.github.io/dpk-news-aggregator/` (GitHub Pages) |
| **Pipeline state** | Cloudflare R2 `state/kcna.db`, `state/translations.json` — **not configured yet**, so runs currently start from an empty database |
| ⚠️ `~/Desktop/DPRKLearning/` | **Stale pre-conversion copy. Do not edit or diagnose from it.** No `.git`, no workflows, no `publish.py`/`rssfeed.py`/`tcache.py`, code frozen before the no-Docker conversion, `data/kcna.db` last written 2026-10-11 |

That stale folder is not pure garbage: it still holds the only local `.env` (with
`TRANSLATE_API_KEY`) and the only local `data/kcna.db` + `data/media` mirror. Move those
two into this repo if you want them locally, then the rest of it can go. Everything in
its `docs` sense was already carried here — `docs/ARCHITECTURE.md`,
`docs/SCRAPING_NOTES.md`, `docs/DPRKLearning-README.md`.

## 🏗️ How it actually runs

* **GitHub Actions is the machine and the scheduler** (`.github/workflows/scrape.yml`,
  cron `17 */6 * * *`): scrape → translate → analyse → `python -m app.publish --out site`
  → deploy `site/` to Pages. No server, nothing long-lived, nothing paid.
* **Pages is static-only**, so `app/web.py` (FastAPI) is a *local viewer* and is never
  deployed. `app/publish.py` writes the same information out as JSON and `assets/app.js`
  renders it — no framework, no build step.
* **`site/` is the only deployable directory.** Publishing from the repo root would put
  `app/*.py` on the public internet, which is why neither host uses git-integration
  builds. `publish.py::_copy_static()` copies in `index.html` and `assets/` only; the
  workflow stages `data/media/` into `site/media/`.
* **Docker is retired** (`legacy-docker/`). Anything in a note or doc that mentions
  `docker compose`, `app/static/`, Jinja templates or a `/static/placeholders` URL is
  describing the old design; the equivalents are `app.scrape --once`, `assets/`, and
  static JSON.

## 📰 Sources — DPRK-specific only

KCNA, Rodong Sinmun, Voice of Korea, the korean-books magazines, 38 North and Daily NK.
No HRW, no AP, no Reuters, no general international news. (Older notes listing "NK News"
as a source are out of date; it is not wired up.)

## 🎨 Design: "Minimalist newsroom"

Newspaper-broadsheet look: serif body (Noto Serif), red `#CC0000` rules, slate/gray
palette, grid cards, category badges, source + timestamp on every item. Tailwind and
fonts come from a CDN, so without access to them the site is unstyled text (the data
path does not need them). Tailwind breakpoints in use: `md:` 768px (hamburger nav),
`lg:` 1024px (desktop grid). Scraped text is only ever set via `textContent`, never
re-parsed as markup.

## 🔧 Working on it

```bash
git status                        # from the repo above; main is the deployed branch
python -m pytest tests -q         # needs requirements-scrape.txt for 3 test modules
python -m app.publish --out site && python -m http.server -d site
gh workflow run scrape.yml        # publish now instead of waiting for cron
```

Two conventions that have already caused bugs, so they are worth repeating:

* **`assets/` is the deploy surface.** Anything the published site needs must live in
  `index.html` or `assets/`; a new `app/static/` folder ships nowhere.
* **A run can be green and the site still empty.** Read the `run complete (...)` tally
  and grep the log for `held:` — those are failures. `off-topic` / `no image` skips are
  the topic filter working.

## 📋 Status

Done: pipeline on Actions, Pages deploy, English RSS sources, placeholder cover art for
stories with no photo, honest recency labels (`at` / `at_kind`), CI, probe workflow.

Open: wire up R2 so state persists (see README *Persisting pipeline state*); the
`skipped_no_image` gate still keeps photo-less KCNA/Rodong stories out of the mirror even
though the site now has something to show for them; the `skipped_no_image` history
(259 rows in the old local database) is unreachable behind `.recheck_done_v3`; Tailwind is
still a CDN dependency.

## 🚫 What not to do

* Don't add non-DPRK sources.
* Don't publish from the repository root, and don't add files outside `assets/` that the
  site expects to be served.
* Don't diagnose the live site from `Desktop/DPRKLearning`, and don't assume a local
  process or container is running — there isn't one.
* Don't count on a `data/` directory existing locally; state lives in R2 once configured.

---

*Rewritten 2026-10-26. The previous version of this file described the 2024 static-mock
stage: it pointed at `~/hermes/dpknews/` (a path that no longer exists), a
`*.workers.dev` URL, "plain HTML, auto-deploys from main on push", and listed automated
scraping as a TODO that had long since shipped.*
