# sanskrit-wikisource-atlas

A more accessible interface for the text content at sa.wikisource.org

Served at https://tylergneill.github.io/sanskrit-wikisource-atlas.

# motivation

Sanskrit Wikisource holds more material than most people realize, but it's hard to browse: category and page structures are disorienting to navigate, there's no metadata like filesize, and there's no transliteration for readers who prefer, e.g., IAST over Devanagari.

This project mirrors that structure into a dedicated, searchable, transliteration-aware front-end, making the collection easier to explore. It also recommends ways to improve the source collection.

See blog post at tylerneill.info/blog-kalpataru-diaries

# how it works

Two pipeline commands produce everything the site needs:

- `make process` — builds `docs/data/tree.json`, the current live corpus tree, from the most recent month's snapshot
- `make backfill` — builds `docs/data/changelog.json`, the historical month-over-month changelog

Both are static JSON files consumed directly by the frontend in `docs/` (no build step, no server). See `CLAUDE.md` for full architecture, pipeline stages, and data shape details.
