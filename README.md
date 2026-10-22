# DPRK News Aggregator

A DPRK-only news aggregator. The ingestion, translation and analysis pipeline was
merged in from the standalone `DPRKLearning` project, so the repository now holds
both halves of the product:

- a **static front end** (`index.html`, no build step) served by **Cloudflare Pages**
- a **Python back end** (`app/`) that scrapes DPRK state media, self-translates
  Korean → English, and runs the Agriculture quantity analysis

The project is **not containerised** — no Dockerfile or compose stack is used.
The previous container definitions are kept under `legacy-docker/` for reference
only.

## Layout

| Path | Purpose |
|---|---|
| `index.html` | Cloudflare Pages front end (Tailwind via CDN, no build step) |
| `assets/` | Favicons and logo assets |
| `app/` | Python back end: `scraper.py`, `translate.py`, `rodong.py`, `parser.py`, `agri_*` analysis, `web.py` (FastAPI) |
| `app/data/dict_ko_en.json` | Bundled KO→EN glossary used by the fallback translator |
| `tests/` | `pytest` suite (parser + analysis) |
| `docs/` | Architecture notes and the pre-merge `DPRKLearning` README |
| `legacy-docker/` | Retired Dockerfile / docker-compose.yml — reference only |

Local runtime data (`data/` — SQLite database and downloaded media) and `.env` are
git-ignored. Note that `/data/` is anchored to the repository root on purpose: an
unanchored `data/` pattern would also match `app/data/` and silently drop the glossary.

## Local development

Requires Python 3.11 (the container image pinned 3.11; 3.13 emits deprecation
warnings for `datetime.utcnow()`).

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pytest
copy .env.example .env
pytest
```

`DATA_DIR` selects where the SQLite database and media live; it defaults to `./data`.

## Deploy

Pushing to `main` triggers a Cloudflare Pages build automatically. To force a
rebuild with no content change, bump the version comment at the top of `index.html`
rather than making an empty commit.

---

<!-- push-test: verified Beached89 write access to origin/main from this machine -->

