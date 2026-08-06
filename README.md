# sanskrit-wikisource-atlas

A more accessible interface for the text content at sa.wikisource.org

Served at https://tylergneill.github.io/sanskrit-wikisource-atlas.

# motivation

Sanskrit Wikisource holds more material than most people realize, but it's hard to browse: category and page structures are disorienting to navigate, there's no metadata like filesize, and there's no transliteration for readers who prefer, e.g., IAST over Devanagari.

This project is a navigational layer over that structure — a dedicated, searchable, transliteration-aware front-end that hosts no text of its own and links back to sa.wikisource.org for the content itself. It also surfaces structural problems in the source collection, and recommends ways to fix them upstream.

See the blog post at https://tylerneill.info/blog/sanskrit-wikisource-and-atlas.

# how it works

A Makefile provides key commands:

- `make refresh-dump` — downloads the newest sa.wikisource dump
- `make process` — parses the downloaded dump and builds `docs/data/tree.json`, the main page's browsable tree
- `make backfill` — walks the full range of historical monthly snapshots and fills any gaps (caches expensive content calculations, otherwise takes many hours to run from scratch)
- `make regen-changelog` — builds `docs/data/changelog.json`, the month-over-month history of how the collection has changed
- `make audit-update-about` — finds structural problems (with breadcrumb, categories, transclusions, etc.) and updates the relevant section of `docs/about.html`
- `make serve` — serves `docs/` locally

The site's own About page documents how the collection is modeled and what the data reports mean. See `CLAUDE.md` for full architecture, pipeline stages, and data shape details.
