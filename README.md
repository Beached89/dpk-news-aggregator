# DPRK News Aggregator

A DPRK-focused news aggregator: a static front-end deployed to **Cloudflare Pages**,
with a scraping / translation back end deployed to **Cloudflare Workers**.

See [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for the working notes carried over from
the static-site phase.

## Layout

| Path | Purpose |
|---|---|
| `index.html` | Cloudflare Pages front end (no build step) |
| `assets/` | Favicons and logo assets |
| `worker/` | Cloudflare Workers API (in progress) |

## Deploy

Pushing to `main` triggers a Cloudflare Pages build automatically. To force a
rebuild with no content change, bump the version comment at the top of
`index.html`.

---

<!-- push-test: git identity + remote write access verified from this machine -->
