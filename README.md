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
- `make process` — builds `docs/data/tree.json`, the current corpus tree, by parsing the downloaded dump: it resolves the Category graph and Main-namespace breadcrumb hierarchy, folds in ProofreadPage Index/Page items and their transclusions, and computes per-item size and page/text counts.
- `make backfill` — builds `docs/data/changelog.json`, the historical month-over-month changelog, by walking the full range of monthly snapshots and diffing consecutive months into per-item added/removed/changed entries. Takes hours on a full run, but is safe to interrupt and rerun — already-downloaded and materialized months are reused rather than redone.
- `make audit-update-about` — reports structural problems (breadcrumb gaps, orphaned and red-link categories, cycles, broken Commons transclusions) for a human to fix on-wiki, and regenerates the audit findings section of `docs/about.html`
- `make serve` — serves `docs/` locally

The site's own About page documents how the collection is modeled and what the data reports mean. See `CLAUDE.md` for full architecture, pipeline stages, and data shape details.
