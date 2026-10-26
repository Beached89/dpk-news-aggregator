# Placeholder image pool

Generic cover art for stories the source published **without a photo** (most
38 North and Daily NK items arrive as text only), so the front page isn't a wall
of "no photo in source" boxes.

* Served at `assets/placeholders/ph-NN.jpg`. The pool is committed here and
  `publish.py::_copy_static()` copies all of `assets/` into `site/`, so Pages
  needs no network at runtime and the workflow needs no extra step.
* Picked by `app/placeholders.py`: the story id decides the image, so one story
  keeps the same art across the 6-hourly runs instead of reshuffling on every
  deploy. `PLACEHOLDER_SALT` re-rolls every assignment at once.
* 800×450 JPEGs — the card is 16:9, so they fill it without letterboxing.
* Kept washed-out in CSS (`.cover-wrap.cover-ph img` in `index.html`) so a
  decorative image can never be mistaken for a photo of the story. Delete the
  `filter`/`opacity` there for full colour. Cards only: the article page shows
  real source media or nothing.

## Refreshing the pool

```bash
python -m app.placeholders                       # fill any missing slots (50)
python -m app.placeholders --count 60 --force    # re-scrape everything
python -m app.placeholders --replace-slots 7,31  # swap out individual slots
python -m app.placeholders --replace-slots 7 --ids 1039   # pick a specific photo
```

The builder pulls from [Lorem Picsum](https://picsum.photos), caps each
photographer at two images so the pool stays varied, and refuses anything that
isn't a decodable image.

**Always eyeball the result before committing** — some stock photos carry faces,
readable text or branding, none of which belong under a news headline. Tile them
into one sheet (Pillow) and delete/replace the offenders:

```python
from PIL import Image
from pathlib import Path
files = sorted(Path("assets/placeholders").glob("ph-*.jpg"))
cols, tw, th = 10, 200, 112
rows = -(-len(files) // cols)
sheet = Image.new("RGB", (cols * tw, rows * (th + 16)))
for i, f in enumerate(files):
    im = Image.open(f); im.thumbnail((tw, th))
    sheet.paste(im, (i % cols * tw, i // cols * (th + 16)))
sheet.save("contact_sheet.jpg", quality=88)
```

## Licence & provenance

`manifest.json` records the Picsum id, photographer, source URL and sha256 of
every file. Lorem Picsum's photos are free to use without attribution
(https://picsum.photos/license).

`manifest.json` also carries `retired_ids`: photos rejected by review. They are
never offered again, so a rejected image can't creep back in on a later build.
