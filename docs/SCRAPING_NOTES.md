# Scraping Spec (validated live via browser session)

---

## Rodong Sinmun (rodong.rep.kp) — scheme (investigated 2026-06-14)

PHP site; navigation is `index.php?<base64-blob>` (a single opaque query value).

- **Article links:** the blob base64-decodes to `12@<YYYY-MM-DD-seq>@...`
  (e.g. `12@2026-06-10-014@...`). The base64 starts with `MTJA` (= "12@").
  The **publication date is embedded in the id**, so the 48h cutoff and the
  KCNA↔Rodong dedup can both use it without fetching.
- **Category links:** blob decodes to `1@@<section>@...` (starts `MUBA`/`MUA`).
  Sections seen: 혁명활동소식, 인민을 위한 정치, 전진하는 조선, 사회문화생활,
  유구한 력사·찬란한 문화, 사진(photos), 동영상(video).
- **Scraper treats the blob as opaque** — extract `<a href="index.php?...">`
  from listing pages and follow them; no need to construct blobs.
- **Article page selectors:** title `.TitleP`, body paragraphs `.TextP`,
  container `.article-content`, photos under `.photo-container`.
- **Caveats:** pages are heavy (~450KB) and the servers are slow/flaky — needs a
  longer timeout + retries; one inspection froze the renderer.
- **Dedup with KCNA:** match on `published_date` + normalized title (KCNA and
  Rodong run the same wire stories with the same headline).

---

# KCNA Scraping Spec (validated live, 2026-06-13)

All of the below was confirmed against the live site via a real browser session.

## Rendering
- **Article, category, and image pages are server-rendered HTML.** A plain HTTP
  client (`httpx`) sees full content — **no headless browser / Playwright
  required.**
- The homepage `/{lang}` raw HTML already contains the article links (36 on the
  KO home, ~15 per article-category page).
- Caveat: the site returned only a shell to a naive external fetch (bad
  User-Agent). Fix: send a **browser-like User-Agent** and **warm up a cookie
  session** by GETting the homepage first, then reuse the cookie jar.

## URL patterns  (`lang` ∈ `kp` `en` `cn` `ru` `es` `jp`)
| Thing            | Pattern |
|------------------|---------|
| Homepage         | `/{lang}` |
| Article          | `/{lang}/article/q/{hash}.kcmsf`  (hash = 32 hex) |
| Article category | `/{lang}/category/articles/q/{hash}.kcmsf` |
| Photo category   | `/{lang}/category/photos.kcmsf` (listing is JS-rendered; skip — photo articles also appear in normal feeds) |
| Image resource   | `/{lang}/image/q/{longhash}.kcmsf`  (downloadable directly) |
| Video resource   | `/{lang}/video/q/{hash}.kcmsf`  (assumed; verify on first video article) |

## Selectors (article page)
| Field      | Selector | Notes |
|------------|----------|-------|
| Title      | `.article-main-title` | |
| Subtitle   | `.article-sub-title`  | often absent |
| Body       | `.article-content-body` | paragraphs are `<p>` inside `.content-wrapper` |
| Date       | regex `20\d{2}\.\s?\d{1,2}\.\s?\d{1,2}` | e.g. `2026.06.10`; dateline para like `(평양 6월 10일발 조선중앙통신)` |
| Media      | any `img` whose `src`/`data-src` matches `/{lang}/image/q/…kcmsf` | exclude `/assets/…` (logo) |

## KO ↔ EN linking  — **trivial**
The article **hash is identical across languages**. The Korean article
`/kp/article/q/<hash>.kcmsf` and its English translation
`/en/article/q/<hash>.kcmsf` share the exact same `<hash>`. Pairing = swap the
language segment. (Confirmed: a KO homepage hash was present verbatim in the EN
article list.)

## Politeness / reliability
Servers are slow and flaky → cap concurrency to 1–2, delay ~1–3 s between
requests, retry with exponential backoff, and make runs resumable (per-article
status in the DB) so a failure never corrupts the mirror.
