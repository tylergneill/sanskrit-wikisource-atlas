# sanskrit-wikisource-atlas

A more accessible interface for the text content at
[sa.wikisource.org](https://sa.wikisource.org), one of the
[`Sāgarasaṅgama`](https://github.com/tylergneill/sagara-sangama) Atlases.

Served at https://tylergneill.github.io/sanskrit-wikisource-atlas.

Its [about page](https://tylergneill.github.io/sanskrit-wikisource-atlas/about.html)
is the user-facing documentation — how the collection is modeled and what the
data reports mean. `CLAUDE.md` has the architecture, pipeline stages and data
shape, and `notes/` the working backlog and the evidence behind it. The rest of
this file covers only how to build it.

# motivation

Sanskrit Wikisource holds more material than most people realize, but it's hard
to browse: category and page structures are disorienting to navigate, there's no
metadata like filesize, and there's no transliteration for readers who prefer,
e.g., IAST over Devanagari.

This project is a navigational layer over that structure — a dedicated,
searchable, transliteration-aware front-end that hosts no text of its own and
links back to sa.wikisource.org for the content itself. It also surfaces
structural problems in the source collection, and recommends ways to fix them
upstream.

See the blog post at https://tylerneill.info/blog/sanskrit-wikisource-and-atlas.

# how it works

A `pipeline/` turns a downloaded Wikimedia dump into `docs/data/tree.json`; a
single-page frontend in `docs/` browses it. Nothing here scrapes the site: the
dump is a published export, so the whole build is offline once it has landed.

- `make refresh-dump` — downloads the newest sa.wikisource dump, verifying and
  decompressing whatever is missing or stale (`refresh-dump-force` redoes every
  part file)
- `make process` — parses the dump → `docs/data/tree.json`, the main page's
  browsable tree. Override worker count with `WORKERS=`
- `make backfill` — walks the full range of historical monthly snapshots and
  fills any gaps. Caches expensive content calculations; takes hours from
  scratch, and is safe to interrupt and rerun
- `make regen-changelog` — builds `docs/data/changelog.json`, the
  month-over-month history of how the collection has changed
- `make audit` / `make audit-update-about` — finds structural problems (with
  breadcrumb, categories, transclusions, etc.); the second also rewrites that
  section of `docs/about.html`
- `make verify` — checks that the artifacts committed under `docs/` agree with
  each other, most importantly that `changelog.json` covers the dump `VERSION`
  claims to publish. The deploy workflow runs this as a gate, so running it
  before pushing catches the same problems early
- `make serve` — serves `docs/` locally on :8001

`make extract-text` also writes the corpus out as files, and is the one target
that needs the private `rivulet` package — it exits 2 ("machinery not
installed") without it. That is a publishing act rather than a build step, so
everything above runs either way.

# license

[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.en),
matching `Sāgarasaṅgama`. Applies to this atlas's own code and derived metadata;
the texts themselves belong to sa.wikisource.org and its contributors.
