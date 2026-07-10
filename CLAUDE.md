# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A more accessible browsing interface for the Sanskrit text collection at sa.wikisource.org. Wikisource's category structure is hard to browse (no good overview for non-technical users, disorienting subcategory nesting, no metadata like filesize, no transliteration). This project scrapes the category tree via the MediaWiki API and renders it as a static, searchable, transliteration-aware site published to GitHub Pages from `docs/`.

## Architecture

Two independent halves connected by one generated JSON file:

1. **Scraper** (`scrape.py`) — a single-file Python script that walks the MediaWiki category API starting from `वर्गः:ग्रन्थाः` (and separately injects `वर्गः:धर्मशास्त्रम्` as a top-level sibling, since it isn't actually nested under ग्रन्थाः on the source site). It recursively builds a `CatSkeleton` tree (categories + pages), fetches page byte-sizes in a batched second pass, computes recursive size/count stats per category, and writes the result to `docs/data/tree.json`. This is the *only* input the frontend consumes.

2. **Frontend** (`docs/`) — a static, dependency-free vanilla JS app (`app.js`) that fetches `data/tree.json` client-side and renders a two-pane UI: an expandable/collapsible sidebar tree and a main content pane. No build step, no bundler, no framework — `docs/` is served as-is by GitHub Pages. The only external dependency is the Sanscript CDN script (loaded in `index.html`) used for on-the-fly Devanagari → IAST/ITRANS/HK/ISO/SLP1 transliteration, applied purely in the browser (source data is always stored in Devanagari).

Because the frontend has no build step, `docs/` (including `docs/data/tree.json`) is what's actually deployed — regenerating `tree.json` and committing it *is* the deploy step for content updates.

## Commands

Run the scraper (regenerates `docs/data/tree.json`):
```
make scrape
```
equivalent to:
```
python scrape.py --verbose
```

Useful scraper flags:
- `--out PATH` — output path (default `docs/data/tree.json`)
- `--delay SECONDS` — delay between API requests (default `0.5`; the scraper is deliberately polite/single-threaded against the live Wikisource API)

There is no test suite, linter, or build step in this repo. To preview the frontend locally, serve `docs/` with any static file server (e.g. `python -m http.server`) from within that directory, since `app.js` fetches `./data/tree.json` via a relative path (won't work from `file://`).

## Key data shape (`docs/data/tree.json`)

```
{ "root": Node }

Node (category/collection):
  { id: "cat:<title>", type: "category"|"collection", title, children: [Node], pages: [Page], stats: { bytes, count } }

Page:
  { id: "page:<title>", type: "page", title, url, stats: { bytes } }
```
- `title` fields are raw Devanagari (the `वर्गः:` / `Category:` namespace prefix is stripped); the frontend transliterates on render, never the scraper.
- `stats` on a category node are recursive totals over all descendant pages.
- The scraper hardcodes an exclusion list of Wikisource maintenance/junk categories (e.g. `निष्कासनाय`, `अनिर्दिष्टानि पुटानि`) — if new junk categories appear in scraped output, add them to that list in `build_skeleton()` rather than filtering in the frontend.

## Notes

- `docs/VERSION` holds a single `__version__ = "x.y.z"` line displayed in the UI header; bump it manually when making user-visible frontend changes.
- `tools/` is scratch/exploratory work (Jupyter notebooks, cached HTML, earlier scraping approaches) and is untracked/gitignored-in-spirit — do not treat it as part of the maintained codebase.

## Known gap: scraper misses MediaWiki subpages (the "conservatism problem")

`build_skeleton()` discovers pages *only* via `list=categorymembers` recursion. It has no logic to follow MediaWiki's `Title/Subtitle` subpage convention, so any work organized as an index/ToC page with real content living in `/`-delimited subpages is badly undercounted — the scraper sees only the shallow index page and none of its children, even though the index page itself was reached correctly through the category tree.

Concretely confirmed with नैषधीयचरितम् (Naiṣadhīyacarita), which exists on Wikisource in two organizational forms:
- One version is tagged directly into category `वर्गः:महाकाव्यम्` as 6 flat pages (e.g. `नैषधीयचरितम् सर्गाः १-५`), ~877KB total — this one the scraper captures correctly, since categorymembers sees all 6.
- A second version is a single page titled `नैषधीयचरितम्‌` (note: has a trailing U+200C ZWNJ in its actual title — copy links carefully, a plain `नैषधीयचरितम्` lookup 404s) that is itself categorized into `महाकाव्यम्` but contains only a 2.1KB अनुक्रमणिका (table of contents) linking to 22 subpages named `नैषधीयचरितम्‌/प्रथमः सर्गः`, `नैषधीयचरितम्‌/द्वितीयः सर्गः`, ... `नैषधीयचरितम्‌/द्वाविंश: सर्गः`, each holding real verse text (~40-50KB apiece, comparable in total size to the first version). None of these 22 subpages are categorymembers of anything — they're invisible to the current scraper. The tree currently reports this whole second version as a single 2.1KB stray "page" with no real content.

Fix direction (roadmap item #2, "descend more levels"): for every page the scraper visits, also query `list=allpages&apprefix=<title>/` (or `generator=allpages` with the same prefix) to discover and pull in subpages, recursively. This is a separate mechanism from category recursion and needs to run regardless of whether a page was reached via a category.

## Known gap: index/ToC page timestamps do not reflect child-page edits (datestamping)

Investigated for roadmap item #4 ("compile overall latest-change datestamps for each item"). Question: for a multi-part work fronted by a manually-created index/ToC page, does MediaWiki's own `timestamp` on that index page ever update when a linked child/subpage is edited? Answer: **no**. An index page's revision timestamp reflects edits made directly to that page (its link list, formatting, category tag) and nothing else — it is not a reliable freshness signal for the work as a whole. Any "last changed" stat for a work must be computed by the scraper as `max()` over the timestamps of all actual content pages/subpages, not read off the index page.

Confirmed via `prop=revisions&rvprop=timestamp` against both नैषधीयचरितम् organizational variants (see above):

**Case 1 — `नैषधीयचरितम् सम्पूर्णम्` (index, 433 B) and its 5 linked `सर्गाः` pages, all under category node नैषधीयचरितम्:**

| Page | Last changed | Size |
|---|---|---|
| नैषधीयचरितम् सम्पूर्णम् (index) | 2015-10-26T07:08:01Z | 433 B |
| नैषधीयचरितम् सर्गाः १-५ | 2024-10-10T07:38:28Z | 199,386 B |
| नैषधीयचरितम् सर्गाः ६-१० | 2015-10-26T07:26:50Z | 201,215 B |
| नैषधीयचरितम् सर्गाः ११-१५ | 2018-11-01T07:14:38Z | 180,089 B |
| नैषधीयचरितम् सर्गाः १६-२० | 2015-10-26T07:27:46Z | 211,707 B |
| नैषधीयचरितम् सर्गाः २०-२२ | 2015-10-26T07:37:09Z | 104,699 B |

The index's own timestamp (2015-10-26) predates the most recent real edit (सर्गाः १-५, 2024-10-10) by 9 years.

**Case 2 — `नैषधीयचरितम्‌` (index, has trailing U+200C ZWNJ in its title, 2,140 B) and its 22 linked `/सर्गः` subpages:**

| Page | Last changed | Size |
|---|---|---|
| नैषधीयचरितम्‌ (index) | 2018-03-01T14:29:02Z | 2,140 B |
| नैषधीयचरितम्‌/प्रथमः सर्गः | 2012-06-27T11:06:58Z | 49,318 B |
| नैषधीयचरितम्‌/द्वितीयः सर्गः | 2012-06-28T10:47:19Z | 32,921 B |
| नैषधीयचरितम्‌/तृतीयः सर्गः | 2012-06-27T11:22:44Z | 47,030 B |
| नैषधीयचरितम्‌/चतुर्थः सर्गः | 2012-06-27T11:22:58Z | 39,367 B |
| नैषधीयचरितम्‌/पञ्चमः सर्गः | 2012-06-27T11:09:08Z | 42,884 B |
| नैषधीयचरितम्‌/षष्ठः सर्गः | 2012-06-27T11:09:30Z | 37,734 B |
| नैषधीयचरितम्‌/सप्तमः सर्गः | 2012-06-28T10:46:41Z | 36,893 B |
| नैषधीयचरितम्‌/अष्टमः सर्गः | 2012-06-28T10:46:18Z | 36,233 B |
| नैषधीयचरितम्‌/अष्ठमः सर्गः (misspelled duplicate) | 2012-06-28T10:45:46Z | 113 B |
| नैषधीयचरितम्‌/नवमः सर्गः | 2012-06-27T11:12:27Z | 54,995 B |
| नैषधीयचरितम्‌/दशमः सर्गः | 2012-06-27T11:12:48Z | 46,539 B |
| नैषधीयचरितम्‌/एकादशः सर्गः | 2012-06-27T11:13:08Z | 50,327 B |
| नैषधीयचरितम्‌/द्वादशः सर्गः | 2012-06-27T11:15:33Z | 48,957 B |
| नैषधीयचरितम्‌/त्रयोदशः सर्गः | 2012-06-27T11:15:04Z | 21,632 B |
| नैषधीयचरितम्‌/चतुर्दशः सर्गः | 2012-06-27T11:16:15Z | 35,490 B |
| नैषधीयचरितम्‌/पञ्चदशः सर्गः | 2012-06-27T11:21:41Z | 36,171 B |
| नैषधीयचरितम्‌/षोडशः सर्गः | 2012-06-27T11:21:54Z | 45,875 B |
| नैषधीयचरितम्‌/सप्तदशः सर्गः | 2024-01-23T16:24:14Z | 58,438 B |
| नैषधीयचरितम्‌/अष्टादशः सर्गः | 2012-06-27T11:20:45Z | 48,221 B |
| नैषधीयचरितम्‌/एकोनविंश: सर्गः | 2012-06-27T11:20:57Z | 32,311 B |
| नैषधीयचरितम्‌/विंश: सर्गः | 2012-06-27T11:19:33Z | 42,508 B |
| नैषधीयचरितम्‌/एकविंश: सर्गः | 2012-06-27T11:19:49Z | 56,304 B |
| नैषधीयचरितम्‌/द्वाविंश: सर्गः | 2012-06-27T11:20:01Z | 54,924 B |

Here the index's own timestamp (2018-03-01) falls in the *middle* of the child edit range and still misses the outlier child (सप्तदशः सर्गः, edited 2024-01-23) by 6 years — confirming the index timestamp is just noise from unrelated edits to the index page itself (e.g. its category tag), not a rollup of anything.

Side finding, orthogonal to datestamping but relevant to roadmap item #3 (consolidation): `नैषधीयचरितम्‌/अष्ठमः सर्गः` is a 113-byte near-empty misspelled duplicate of `नैषधीयचरितम्‌/अष्टमः सर्गः` (36,233 B) — one more instance of the duplication problem described in [Wikisource data-quality notes], now confirmed at the subpage level too, not just at the top-level-work level.
