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

# KCNA Scraping Spec (re-validated live, 2026-10-23)

All of the below was confirmed against the live site with a plain HTTP client.
**The site was rebuilt.** Everything in the previous spec (`.kcmsf` routes,
`.article-main-title` / `.article-content-body` selectors, the
`/{lang}/image/q/<hash>.kcmsf` media pattern, the photo *section*) matches
nothing today; that older note is kept only to explain rows already in the DB.

## Rendering
- Article, gallery, video and listing pages are **server-rendered HTML**; no
  headless browser is needed. `User-Agent` still matters (a naive client gets a
  shell), and the session is warmed by GETting `/{lang}` first.
- The homepage is ~1.19 MB: the lead items embed base64 thumbnails, which is why
  a plain `GET /kp` is slow but complete.

## URL patterns  (`lang` ∈ `kp` `en` `cn` `ru` `es` `jp`)
| Thing            | Pattern |
|------------------|---------|
| Homepage         | `/{lang}` |
| Article          | `/{lang}/article/detail/<32 hex>` |
| Category listing | `/{lang}/article/list/<32 hex>` |
| Photo page       | `/{lang}/gallery/detail/<32 hex>` (behind the article's camera button) |
| Photo listing    | `/{lang}/gallery/list/<32 hex>` |
| Video page       | `/{lang}/video/detail/<32 hex>` |
| Image resource   | `/photo/<long hex>` — **no language segment** |
| Video resource   | `/{lang}/video/stream/<long hex>` (`<source type="video/mp4">`, plus an ogg twin) |

The 32-hex article id is **identical across languages**, so the English twin of
`/kp/article/detail/<hash>` is `/en/article/detail/<hash>`.

## Two traps
1. **`/photo/…` is Referer-guarded.** Without a `Referer` from a kcna.kp page the
   origin answers `200 OK`, `Content-Type: ` empty, **0 bytes**. With any kcna.kp
   referer (article or gallery page) it returns the real JPEG (~150–250 KB).
   `client.get_bytes(photo_path, referer=article_url)` is therefore mandatory, and
   `_scrape_kcna` logs `0 bytes (referer guard?)` if it ever regresses.
2. **Article pages carry no publication year.** The dateline is
   `(평양 10월 20일발 조선중앙통신)` (Korean) or `Pyongyang, October 20 (KCNA) --`
   (English). The year only appears in the `[2026.10.20.]` stamp beside each link
   on the homepage and listing pages.

## Selectors (article page)
| Field      | Selector | Notes |
|------------|----------|-------|
| Title      | first non-empty `article h1` | the template emits a second, usually empty, `<h1>`; sub-headline when present |
| Body       | `article p` | bare `<p>` siblings inside `article > div.container` |
| Dateline   | first `<p>` | `(평양 …일발 조선중앙통신)` / `Pyongyang, … (KCNA) --` |
| Photos     | `a.gallery_button[href*="/gallery/detail/"]` → that page's `img[src^="/photo/"]` | article pages have **no** `<img>` of their own except the masthead |
| Video      | `source[src*="/video/stream/"]` | mp4 + ogg pair |
| Ignore     | `/images/logo.png`, `/plugins/…`, `/css/…` | chrome, not content |

Headlines wrap fragments in `<nobr>` and a custom `<august_name>` element;
`text()` extraction handles both, but selector work must not assume plain text.

## Listings (homepage and category pages)
```html
<h5 class="block">
  <a href="/kp/article/detail/<hash>">…제목…</a>
  <span class="publish_date"><nobr>[2026.10.20.]</nobr></span>
</h5>
```
The stamp is a **sibling inside the same heading** — read it off the anchor's
parent only, or one date leaks across a whole block. `extract_listing()` returns
`(hash, lang, date, title)`; `discover_listing_map()` crawls the homepage plus
each `article/list` page once and yields categories, per-article category tags and
the date map that `parse_article` consumes as `listing_date`.

Category labels come from the homepage nav (중요소식, 문건, 국내소식, 국제소식,
대외관계, 사회생활, 경애하는 김정은동지의 혁명활동소식, 혁명일화, 인민은 못
잊습니다, 최신소식, plus event sections), which `CATEGORY_EN` maps to stable keys.

## Pagination — not implemented
Listing pages hold 15 items and carry `var total = 528; var page_cnt = 36;`, but
the pager is built client-side and posts `page_num`/`cnt_per_page`/`keyword`/
`_csrf` to the same list URL (a `GET ?page_num_all=2` is ignored; a `POST`
without the page's CSRF token returns an error fragment). The homepage plus the
first page of each category is more than a 5-day window needs, so deeper pages
are deliberately out of scope.

## Politeness / reliability
Servers are slow and flaky (a connection attempt can time out outright) → cap
concurrency to 1, delay ~2 s between requests, retry with exponential backoff,
and keep runs resumable via per-article DB statuses so a failure never corrupts
the mirror.
