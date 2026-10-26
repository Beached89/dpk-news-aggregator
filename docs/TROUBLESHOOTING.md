# Troubleshooting

**"The articles are not updating"** is the most common report and, so far, every
instance has been a *reading* problem rather than a pipeline problem: the export was
fresh and the label on the card was misleading. Work down this list; each step says what
a good answer looks like. Everything below was run against this repository on 2026-10-26.

There is no local server and no Docker container to check — see *How it runs* in the
README. If you are looking for `docker compose`, that setup is retired in
`legacy-docker/` (and `~/Desktop/DPRKLearning/` is an untracked pre-
conversion copy of the project; do not diagnose against it — it has no workflows, no
`publish.py`, and a database last written 2026-10-11).

## 1. What is the site actually serving?

```powershell
Invoke-RestMethod https://beached89.github.io/dpk-news-aggregator/data/manifest.json |
  Select-Object generated_at, stories, translated
```

```
generated_at          stories translated
------------          ------- ----------
2026-10-26T13:24:05Z       17         17
```

`generated_at` is the moment the last successful pipeline wrote the export. If it is
recent, the pipeline is fine and the complaint is about presentation — go to step 5.
Pages serves the file as-is, but do a hard refresh first if the *page* looks older than
this number.

## 2. Did the pipeline run?

```powershell
gh run list --workflow scrape.yml --limit 5
gh run view <run-id> --log | Select-String -Pattern 'run complete|held:|off-topic|no image'
```

`scrape.yml` is cron `17 */6 * * *` (00:17 / 06:17 / 12:17 / 18:17 UTC). Cron on free
accounts is best-effort and can start late; to run it now:

```powershell
gh workflow run scrape.yml                     # or Actions -> Run workflow
gh run watch                                   # follow the newest run
```

The `run complete (...)` tally is the honest summary. `off-topic, skipped` and
`no image, skipped` are `TOPIC_FILTER` working as designed; **`held:` lines are
failures** — an article cleared the filters and then errored, so it stays hidden and is
retried forever while the run still reports success.

## 3. Is the deploy current?

```powershell
gh api repos/Beached89/dpk-news-aggregator/pages --jq '{build_type, url}'
```

Expect `build_type: workflow`. The `pages` job deploys `site/` only after a green
`pipeline`, and only on `main`; on a branch the run stops at the `site` artifact.
If a run went green but the manifest is old, look at the `pages` job in that run — a
`skipped` here means the step before it did not succeed.

## 4. Is state persisting between runs?

Look for a **`Pipeline state is not persisted`** warning in the run log (and a matching
note in the run summary). With no `R2_BUCKET`/credentials the run scrapes an empty
database, so:

* the site only ever contains what the last few hours found,
* nothing is deduped against earlier runs, so the same wire story can reappear,
* the translation cache starts cold and the same text is retranslated four times a day.

That is a supported way to run, but from outside it is also what a "stale" or "shuffling"
feed looks like. Setup: README, *Persisting pipeline state (R2)*.

```powershell
gh variable list        # R2_ENDPOINT, R2_BUCKET, MAX_AGE_HOURS, SCRAPE_*
gh secret list          # R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
```

## 5. Fresh data, but the cards say it is old

Recency labels are deliberately conservative, because an invented hour is worse than a
coarse one. `date` is a publication **day**; minutes are only counted when the source
stated the moment (`at_kind: "published"` — the RSS feeds). Everything else shows
`today` / `yesterday` / `N days ago`.

```powershell
$a = Invoke-RestMethod https://beached89.github.io/dpk-news-aggregator/data/articles.json
$a.stories | Select-Object date, at, at_kind, sources, title_en | Format-Table -AutoSize
```

Before 2026-10-26 every card was labelled by counting minutes from `published_date` at
midnight UTC, so all of today's stories read "16 hours ago" at 16:00 and the feed looked
dead by lunchtime. See README, *How much the timestamp is telling you*.

## 6. Local database (only if you scrape locally)

```powershell
python -c "import sqlite3;c=sqlite3.connect('data/kcna.db');print(list(c.execute('select max(scraped_at),max(published_date),count(*) from articles')))"
python -c "import sqlite3;c=sqlite3.connect('data/kcna.db');[print(r) for r in c.execute('select status,count(*) from articles group by status order by 2 desc')]"
```

| `status` | Means | On the site |
|---|---|---|
| `fetched` | body stored, passed the topic and photo filters | yes |
| `processing` | stored, awaiting the rest of the pass (media, tags, translation) | no |
| `discovered` | URL seen, body not yet fetched | no |
| `failed` | fetch or parse error, retried on later runs | no |
| `skipped_no_image` | the source published no photo for it — terminal, see below | no |
| `skipped_old` | outside `MAX_AGE_HOURS`; also where `prune_old()` demotes ageing stories | no |

`skipped_no_image` is a hard gate written before the site had anything to show in that
case. The placeholder pool (`app/placeholders.py`) now exists, so if the feed feels too
thin, relaxing that gate is a deliberate one-line-per-source change — not a bug. Note it
also means a photo-less story never reaches the site today, so placeholders appear mostly
on the sources that arrive through paths without the gate.

## 7. The origins themselves

```powershell
foreach ($u in 'http://www.kcna.kp/kp','http://www.rodong.rep.kp/ko/') {
  try {
    $r = Invoke-WebRequest -Uri $u -TimeoutSec 60 -UseBasicParsing `
          -UserAgent 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    '{0} -> {1} ({2} bytes)' -f $u, $r.StatusCode, $r.RawContentLength
  } catch { '{0} -> {1}' -f $u, $_.Exception.Message }
}
```

`.kp` name resolution is intermittently flaky from home connections — a first request
that fails DNS and a second that succeeds is normal, which is why the client retries
instead of treating one failure as downtime. CI reachability is proven separately by
`probe.yml`. If the origins answer 200 and runs are green, the pipeline is working.

