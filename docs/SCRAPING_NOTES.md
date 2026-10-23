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

# External English sources — 38 North & Daily NK (validated live, 2026-10-23)

Both are ordinary hosted WordPress sites, not DPRK infrastructure, and both
publish **full-text RSS** — so these come in through `app/rssfeed.py` and
`scrape_rss()` rather than by crawling page markup.

| | 38 North | Daily NK (English) |
|---|---|---|
| Feed | `https://www.38north.org/feed/` | `https://www.dailynk.com/english/feed/` |
| robots.txt | `User-agent: *` / `Disallow:` (nothing) | disallows only `/wp-admin/`, `/?s=`, `/search/` |
| Feed shape | RSS 2.0, 8 items | RSS 2.0, 10 items |
| Body | `content:encoded`, 8–24 KB HTML | `content:encoded`, 8–18 KB HTML |
| Metadata | `dc:creator`, `<category>`, RFC 2822 `pubDate` | same |
| Item images | 8–10, hotlink fine, **no Referer needed** | 0–2 — many items have none |

## Traps, all reproduced by tests in `tests/test_rssfeed.py`

* **`<figure>` holds the article images.** WordPress wraps every photo in
  `figure.wp-block-image`, so the pass that extracts *text* (which drops figures
  and their captions) must not be the pass that extracts *images*. Using the
  text stripper for both returns a satellite-imagery piece with zero photos —
  observed live, and now covered by `strip_media_chrome()`.
* **Hangul asset paths.** Daily NK serves uploads from Korean directory names; a
  raw IRI dies on ASCII-only sockets ("'ascii' codec can't encode characters"),
  so `encode_iri()` percent-encodes the path and is idempotent on already-escaped
  URLs.
* **Category soup.** Both feeds write categories *and* free post tags into the
  same `<category>` elements with no `domain` attribute to separate them (checked
  on both), and reporters get tagged by name — one item carried 15 labels. The
  leading labels are the editor's sections, so `tag_labels()` keeps those, drops
  labels that merely repeat the byline, and caps at `RSS_MAX_CATEGORIES`.
* **httpx base URL + leading slash.** Per RFC 3986 a leading-slash path replaces
  the base path, so a client based on `…/english` fetching `/feed/` hits the
  *Korean* feed at the site root. Clients are therefore built on the origin and
  feed paths are origin-relative (`/english/feed/`).
* **`tag_article_categories()` targets the Korean row.** English-only stories
  have none, so tags were dropped in silence; `_rss_tag()` attaches to whichever
  rows of the story exist.
* **No photo requirement, no topic filter.** Daily NK often publishes without an
  image (the viewer shows "no photo in source"), and `topics.is_on_topic()` is
  tuned for KCNA's self-reliance beat — applying it here would discard nearly
  every item.
* **`content:encoded` is a whole page**: scripts, embedded tweets as iframes, ad
  markup and the trailing "The post … appeared first on …" line are stripped, and
  paragraph text is joined with blank lines, which is the contract `publish.py`
  and the viewer's `paraSplit()` expect.

## Identity and translation direction

There is no content hash in these feeds, so `article_id()` derives a stable
32-hex id from the canonicalised permalink (scheme/host lower-cased, query and
fragment dropped) — which is what makes re-runs idempotent when WordPress appends
`?utm_source=rss`.

These articles arrive *in English*, so the generated side is Korean:
`translate.translate_to_ko()` mirrors `translate_texts()` (cache-first, batched,
per-line salvage, raises only when nothing works) as its own function so the
KO→EN hot path is untouched. Cache entries are keyed under the `ko` target —
`tcache.key()` takes a `target` override — so the two directions cannot be
confused in the shared cache file. `RSS_TRANSLATE_KO` defaults to **off** (long
analytical pieces against a keyless endpoint); the viewer renders English-only
stories correctly because `titleOf()` falls back across languages.

## Rights and attribution

Unlike the DPRK state feeds, these are copyrighted third-party publications. The
pipeline keeps the canonical URL in `Article.url` and prints
`"38 North · <byline>"` in the existing `dateline` column, and
`RSS_MIRROR_FULLTEXT=0` narrows the mirror to each publisher's own
`<description>` summary with a link out. Full-text mirroring is the default
because this deployment asks for it; switch it off if the mirror is ever made
publicly distributed rather than personal.

