# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A more accessible browsing interface for the Sanskrit text collection at sa.wikisource.org. Wikisource's category structure is hard to browse (no good overview for non-technical users, disorienting subcategory nesting, no metadata like filesize, no transliteration). This project builds `docs/data/tree.json` from Wikimedia's monthly XML dump exports and renders it as a static, searchable, transliteration-aware site published to GitHub Pages from `docs/`. It also maintains a historical changelog of how the corpus has grown over time, rendered on the About page.

## Architecture

Three parts connected by generated JSON files:

1. **Pipeline** (`pipeline/`) — a multi-stage Python pipeline, run stage by stage via the `Makefile` targets below, that turns a downloaded MediaWiki XML dump into `docs/data/tree.json`:
   - **Fetch** (`pipeline/fetch.py`) — locates, downloads, verifies, and decompresses the current monthly Content File Export for sa.wikisource.org from `dumps.wikimedia.org/other/mediawiki_content_current/`. Only a 3-month rolling window is available at this endpoint. Discovery has no API: generation starts monthly on the 1st, and a run is only complete once `SHA256SUMS` appears alongside it, so `find_latest_export` checks candidate month directories for that file's presence rather than trusting a listing. The single XML export covers every namespace the pipeline needs (Main, Category, Index, Page, Template, Module) in one download.
   - **Parse** (`pipeline/parse_dump.py`) — stream-parses the dump XML (`iterparse`, one `<page>` at a time, O(1) memory) into per-namespace page records (`DumpIndex`).
   - **Build tree** (`pipeline/build_tree.py`) — constructs the Main-namespace subpage tree (pure tree, split on `/`) and the Category digraph (manually-maintained, not guaranteed acyclic or fully connected — see "Multi-parented categories" below).
   - **Transclusion** (`pipeline/transclusion.py`) — detects ProofreadPage `<pages index="..." />` transclusion links between Main-namespace pages and Index-namespace scan items, and derives content→category membership.
   - **Content size** (`pipeline/content_size.py`) — real per-page size computation: parse wikitext with `mwparserfromhell`, expand templates by looking up and substituting the matching Template page from the same dump, and transliterate via `skrutable` for the IAST byte count. No heuristics/estimates (unlike the retired v1 scraper).
   - **Process** (`pipeline/process.py`) — runs the above in sequence and assembles a single JSON tree (`docs/data/tree.json`). This is the *only* input the frontend consumes for the live corpus view. See "Key data shape" below for the schema.

2. **Frontend** (`docs/`) — a static, dependency-free vanilla JS app. `app.js` fetches `data/tree.json` client-side and renders a two-pane UI: an expandable/collapsible sidebar tree and a main content pane. `about.js` fetches `data/changelog.json` and renders the historical changelog plus trend charts on `about.html`. No build step, no bundler, no framework — `docs/` is served as-is by GitHub Pages. The only external dependency is the Sanscript CDN script (loaded in `index.html`/`about.html`) used for on-the-fly Devanagari → IAST/ITRANS/HK/ISO/SLP1 transliteration, applied purely in the browser (source data is always stored in Devanagari).

3. **Historical backfill / changelog** (`pipeline/backfill.py` and friends) — walks backward through every available historical monthly dump, builds a throwaway `tree.json`-shaped snapshot for each month, and appends a pairwise size/count/item-level diff between consecutive months to `docs/data/changelog.json`. See "Historical backfill and the changelog" below for the full design.

Because the frontend has no build step, `docs/` (including `docs/data/tree.json` and `docs/data/changelog.json`) is what's actually deployed — regenerating these and committing them *is* the deploy step for content updates.

## Commands

```
make refresh-dump         # download/verify/decompress the current monthly dump into dump/
make refresh-dump-force   # same, but force re-download/re-verify/re-decompress
make process               # build docs/data/tree.json from the downloaded dump
make backfill               # walk the full historical range, append to docs/data/changelog.json
make rebuild-trees           # rebuild tree snapshots from cache + redo changelog, skipping dump reprocessing (MONTHS="..." to scope)
make serve                 # serve docs/ locally on port 8000
make ngrok                 # expose the local server via a public ngrok tunnel (for mobile testing)
```

There is no test suite, linter, or build step in this repo. `app.js`/`about.js` fetch their JSON data via relative paths, so `docs/` must be served over HTTP (`make serve`), not opened via `file://`.

The `make` targets above are run by hand today. A GitHub Action driving fetch → process → publish on the dump's own monthly cadence is the intended eventual automation, not yet implemented.

## Key data shape (`docs/data/tree.json`)

```
{ "root": Node }

Node (category):
  { id, type: "category", title, children: [Node],
    pages: [PageNode], index_items: [IndexItemNode], stats }

Node (category-pointer): a second+ filing of a category already emitted
elsewhere in the tree (multi-parent category). Appears inline among its
parent's own `children`, alongside real category nodes.
  { id, type: "category-pointer", title, points_to: <id>, stats }

PageNode (Main-namespace page, filed into this category via its own direct
[[वर्गः:...]] tag):
  { id, type: "page", title, url, stats, subpages: [PageNode] }
  subpages come from the Main-namespace subpage tree (title split on "/").

Node (page-pointer): a second+ filing of a page already emitted elsewhere in
the tree (a page tagged with >1 category directly).
  { id, type: "page-pointer", title, url, points_to: <id> }
  No stats/subpages of its own; resolve via points_to.

IndexItemNode (Index-namespace item with ZERO transclusion anywhere in
Main-namespace content -- i.e. raw/unpublished OCR):
  { id, type: "index-item", title, url, stats }
  Never expandable into individual पृष्ठम्:Title/N (scanned-leaf) rows --
  those are only ever summed into this node's own stats, never listed.

Node (index-item-pointer): a second+ filing of an Index item already
emitted elsewhere in the tree. Same shape/resolution as page-pointer.
  { id, type: "index-item-pointer", title, url, points_to: <id> }

stats: { raw_bytes, content_bytes, transliterated_bytes, count, text_count, last_changed }
```

- `title` fields are raw Devanagari (the `वर्गः:`/`अनुक्रमणिका:` namespace prefix is stripped); the frontend transliterates on render, never the pipeline.
- `stats.raw_bytes` is raw MediaWiki wikitext size — dominated by markup/template/category-tag overhead on short pages, not a meaningful "how much content" number on its own. `stats.content_bytes` is real, locally-computed content size after markup stripping and template expansion. `stats.transliterated_bytes` is the IAST byte count of that same content — the frontend's headline "effective size" figure, since IAST is smaller on disk (~60%) and more common for cross-collection comparison.
- `count` = number of distinct Main pages + Index items reachable from a node, including every subpage individually. Dedup is enforced at build time: the first depth-first occurrence of a category/page/Index-item builds real content and folds its stats into every ancestor's rollup; every later occurrence anywhere else in the tree is emitted as a `-pointer` node instead and skipped when summing ancestor stats — so an item reachable via two paths is counted exactly once, at whichever ancestor its two paths first converge (not only at root).
- `text_count` = number of distinct top-level *texts* reachable from a node — a Main page with no `/`-parent (breadcrumb subpages don't count separately even when independently filed under their own category tag, see "Silent subpage category divergence" below), or an Index item (always top-level). This is what the frontend sidebar shows as the browsable text count, since `count`'s per-subpage granularity overcounts what a reader would call one text.
- The pipeline hardcodes an exclusion list of Wikisource maintenance/junk categories (e.g. `निष्कासनाय`, `अनिर्दिष्टानि पुटानि`) in `parse_dump.py`'s `EXCLUDED_CATEGORIES` — add new junk categories there, not in the frontend.

### Multi-parented categories (`category-pointer`) and multi-filed pages (`page-pointer`)

Wikisource's category graph is not a strict tree: a category can legitimately be filed under more than one parent, and a page/Index item can legitimately carry more than one category tag. Neither occurrence is more "real" or "canonical" than the other — which one ends up holding the actual content in the JSON is purely an artifact of depth-first build order, not a meaningful distinction. The frontend renders every occurrence as independently selectable/expandable with its own real stats, linking sibling occurrences via a "see also" pointer.

### Untranscluded Index items and OCR content

Wikisource's OCR/"Proofreading" workflow stores scanned page images as `Index:` items (namespace `अनुक्रमणिका`), with individual scanned/proofread leaves as `Page:` items (namespace `पृष्ठम्`, titled `Title/N`). When an Index item's content has been transcluded into a real Main-namespace page (via a ProofreadPage `<pages index="..." />` tag), the mirror shows the Main-namespace page as the real content and skips listing the Index item separately, since it would just be a duplicate. When an Index item has **zero** transclusion anywhere in Main content, it's shown as its own `index-item` node, with stats summed from its untranscluded `पृष्ठम्:Title/N` leaf pages (never listed individually — only rolled up into the Index item's own stats).

### Orphan bucket (`असम्बद्धवर्गीकृतम्`)

Any Main page or untranscluded Index item unreachable from the root category (`वर्गसर्वस्वम्`) by category descent — either zero category tags, or tags that only point to categories themselves never filed under any reachable parent — is collected into an artificial top-level category, `असम्बद्धवर्गीकृतम्` ("improperly categorized"), appended as a sibling of the real category tree under root. It's listed and browsable in the frontend, but root's own headline stats deliberately exclude its totals, since it isn't part of the "central," properly-organized corpus.

## Historical backfill and the changelog (`pipeline/backfill.py`)

`docs/data/changelog.json` is an append-only array of pairwise month-to-month comparisons (`pipeline/compare.py`'s `build_report()`), each carrying old/new size and count totals plus item-level added/removed/changed-timestamp lists. The About page (`docs/about.html`/`about.js`) renders this as a browsable history plus trend charts, with a "Granularity: Month/Quarter/Year" control that nets adjacent months together client-side (see `about.js`'s `groupEntries`/`reduceGroup`) — no separate precomputed granularity in the JSON itself.

Building this history requires four different source eras, all handled by `pipeline/backfill.py`:

1. **Current era** (`pipeline/fetch.py` / `mediawiki_content_current`) — only a 3-month rolling window is available.
2. **Legacy era** (`pipeline/fetch_legacy.py`) — the classic MediaWiki export format (`pages-meta-current.xml.bz2`), merged transparently from two sources: Wikimedia's own live rolling window (which overlaps with and bridges up to the current-era window's start) and the Internet Archive (`sawikisource-<YYYYMMDD>` items, real historical depth back to 2011 — though sa.wikisource's Internet Archive volunteer pipeline stalled after 2022-05-01).
3. **Materialized era** (`MATERIALIZED_MONTHS`, `_ensure_materialized_month`) — covers every interior hole in the two legacy sources' combined coverage via the same on-demand mechanism. `compute_materialized_months()` scans `fetch_legacy.list_available_months()` for any `YYYY-MM` with no entry, strictly between the earliest and latest months either source covers, at or after `MATERIALIZED_FLOOR` (2012-01, since `वर्गसर्वस्वम्`'s earliest revision is 2012-01-20 — see `RootCategoryMissing` below for months before that). This isn't a hardcoded list of ranges; it's detected live against `fetch_legacy`'s two sources (itself disk-cached, 24h TTL) each time `pipeline.backfill` is imported. As of this writing it finds five holes: 2012-01 through 2014-06 (Internet Archive's last dump before `वर्गसर्वस्वम्` existed was 2011-10-13; its first dump after real legacy coverage resumes is 2014-07-01), 2015-01, 2015-05, 2018-04 through 2018-07, 2019-04 through 2020-06, and 2022-06 through 2025-10 (the gap between Internet Archive's last snapshot and the live rolling window's start).

   No dump file exists for these months on any source, so `pipeline/materialize_snapshots.py` reconstructs one on demand from `sawikisource-latest-pages-meta-history.xml.bz2` (every surviving revision ever made): for a cutoff date D, a page's state is the newest revision with timestamp ≤ D. Each month's reconstruction is generated on demand, one at a time, right when `ensure_month` needs it, and its raw XML is deleted again immediately after its snapshot is written — never more than one materialized dump on disk at a time. The underlying meta-history dump itself (~533MB) is downloaded once and cached, since re-downloading it is the expensive part. See `pipeline/materialize_snapshots.py`'s docstring for known deviations from a genuine dump of that month, and `pipeline/validate_materialization.py` (kept in the repo for future re-validation, not run automatically) for accuracy validation against real dumps at the era boundaries — confirmed within ~0.5-0.6% on every metric for the 2022-2025 gap; re-run against other era boundaries (2014-07, 2018-03/2018-08, 2019-03/2020-07) before trusting those ranges equally.

For each month, `ensure_month` resolves an exact date to the right era (materialized months checked first, since the other two sources are confirmed to never have them). Raw dumps land in one of four numbered era folders under `dump/`, each holding its own dated subdirectories — `1_current_format_live/<date>/` (era 1, routine `make process`'s live dump — never touched by backfill), `2_legacy_format_live/<date>/` (era 2), `3_materialized/<date>/` (era 3, every `MATERIALIZED_MONTHS` date regardless of which hole it's in), `4_legacy_format_archive/<date>/` (era 4) — see `pipeline/backfill.py`'s `DEFAULT_*_ROOT` constants. Once a month's snapshot is written, its raw dump directory is deleted immediately (`cleanup_raw_dump`) — pass `--keep-raw-dumps` to disable this.

**Genuinely too-early dumps**: sa.wikisource's category system, and later the ProofreadPage extension (Index/Page namespaces), didn't always exist. The oldest available Internet Archive dump (2011-10-13) predates both — confirmed only 3 categories existed on the entire site at that point, none of them `वर्गसर्वस्वम्`. `parse_dump.py`'s `index_ns_id()`/`page_ns_id()` return `None` (not an error) when those namespaces are genuinely absent from a dump's siteinfo, and `process_dump()` raises a distinct `RootCategoryMissing` when the root category itself doesn't exist yet, which `backfill.py`'s `main()` catches and skips (logging a note) rather than aborting the whole run.

`pipeline/run_backfill_sequence.sh` drives `pipeline.backfill` one month-pair at a time (so a failure on one pair doesn't lose earlier progress), starting from the newest anchored current-era month and walking backward through every legacy + materialized month. Safe to interrupt and rerun — already-fetched/materialized dumps, already-built snapshots, and already-logged changelog transitions are all skipped, not redone.

`pipeline/backfill.py` deliberately does NOT write `docs/data/tree.json` or `docs/VERSION` — those reflect the live, current-month pipeline state, not a historical replay.

`docs/data/source_eras.json` (read by `about.html`'s Snapshots section, to describe era 1/era 2's current live-rolling-window start dates) is refreshed by a separate module, `pipeline/update_source_eras.py`, not by `pipeline.backfill` itself — it does two live network lookups (~1 minute total) that have nothing to do with any particular month-pair, so folding it into every `pipeline.backfill` invocation would mean paying that cost on every one of `run_backfill_sequence.sh`'s 150+ per-step calls for no reason. `run_backfill_sequence.sh` runs it once, standalone, after its whole walk finishes.

`pipeline/fetch_legacy.py`'s `list_available_months()` (merged live-rolling-window + Internet Archive month listing, used by `_ensure_legacy_month`, `default_months()`, `update_source_eras`, and `run_backfill_sequence.sh`'s own upfront `--list` call) is genuinely expensive — 2 listing requests plus one more request *per date* in each listing, dozens total, not just 2. Since `run_backfill_sequence.sh` spawns a fresh `python -m pipeline.backfill` subprocess per step, an in-memory cache wouldn't help; it's cached to disk instead (`dump/_fetch_legacy_months_cache.json`, 24h TTL — see `LIST_AVAILABLE_MONTHS_CACHE`/`LIST_AVAILABLE_MONTHS_CACHE_TTL`), so every caller across an entire `run_backfill_sequence.sh` walk shares one query instead of re-deriving the identical listing on every step. Pass `use_cache=False` (or `fetch_legacy --list --no-cache`) to force a fresh query.

### Two on-disk layers per month, and what deleting each one triggers

For each backfilled month, `ensure_snapshot` writes two separate gitignored, gzipped files under `dump/`, plus one shared, git-tracked output:

- **`dump/_backfill_content_cache/content-<date>.json.gz`** (the *input* layer) — the small, cheap-to-derive-but-annoying-to-lose inputs `build_tree_json` needs: per-page byte counts (raw/content/transliterated — the output of `compute_all_content_sizes`, the genuinely slow step: `mwparserfromhell` parsing, template expansion, `skrutable` transliteration), category tags, redirect targets, timestamps, and transclusion results. See `pipeline/content_cache.py`.
- **`dump/_backfill_snapshots/tree-<date>.json.gz`** (the *output* layer) — the fully assembled `tree.json`-shaped snapshot, same schema as `docs/data/tree.json`. What `pipeline/compare.py` actually diffs pairwise.
- **`docs/data/changelog.json`** (git-tracked, not gitignored) — the append-only pairwise diffs between consecutive snapshots, keyed by `(old_date, date)`. This is the only one of the three that's committed and deployed.

`ensure_snapshot` skips a month entirely (both the cache and the tree snapshot) if `tree-<date>.json[.gz]` already exists — so **deleting only the changelog does not trigger any dump reprocessing or snapshot rebuilding**: the next `pipeline.backfill` run (or the next still-running step of `run_backfill_sequence.sh`, since each step re-reads the changelog fresh) will instantly reuse every existing snapshot and just recompute+re-append the pairwise diffs from them (cheap — no XML parsing). One consequence worth knowing: since a missing changelog means `next_id` restarts at 1, entries added after the file was deleted/moved get id numbering that doesn't continue the old sequence — harmless for display (the changelog viewer sorts by `date`, not `id`), but a prior range of months whose entries were in the deleted file needs to be explicitly re-diffed to reappear (see `make rebuild-trees`, or a plain backfill run scoped with `--months` over that range — both cheap, since the snapshots those entries depend on already exist).

Deleting `dump/_backfill_snapshots/*` (tree snapshots) forces a full reprocess for those months on the next plain `pipeline.backfill` run — it does NOT know to consult the content cache automatically; a bare rerun re-fetches and re-parses the dump from scratch regardless of whether a cache file is sitting right next to the deleted snapshot. To make it use the cache instead, pass `--rebuild-trees-only` (or `make rebuild-trees`, optionally scoped via `MONTHS="..."`) — this reads `content-<date>.json.gz`, re-runs only `build_tree_json` (seconds, not minutes), overwrites `tree-<date>.json.gz`, and then re-diffs and **overwrites in place** (matched by `(old_date, date)`, same `id` preserved) any existing changelog entry for that transition — deliberately not skip-if-exists, since the point is picking up corrected stats. This is the fast path for propagating a `build_tree_json`/`build_category_graph`-level logic fix (a rollup/dedup/assembly bug, e.g. the redirect-parenting or subpage-category-divergence fixes) across all 153+ already-backfilled months without waiting 8-10 hours.

Only deleting `dump/_backfill_content_cache/*` (or a month simply predating this cache's introduction) forces the expensive path — `compute_all_content_sizes` reruns from a re-parsed dump XML. This is the one case there's no shortcut for; it's only actually needed for a bug in content-size computation itself (a byte-counting or transliteration defect), not a tree-assembly bug.

In short: **to redo the changelog or the tree snapshots, delete them and rerun** (`--rebuild-trees-only`/`make rebuild-trees` for snapshots, to skip the slow step) — but if you delete tree snapshots and want the cheap rebuild, you must pass `--rebuild-trees-only` explicitly, since a bare rerun won't infer that from the cache's mere presence.

## Notes

- `docs/VERSION` holds `__code_version__` (bump manually on user-visible frontend changes), `__data_version__` (pipeline-run date, stamped automatically by `process.py`'s `_stamp_data_version`), and `__content_version__` (the Wikimedia dump export's own date, also stamped automatically) — three separate dates, since a pipeline run's date and the dump's own snapshot date can differ.
- `notes/` holds prototype/spec material not yet absorbed into the maintained codebase, and one-off historical analysis scripts kept for the record (not meant to be re-run routinely).
- **Deliberate non-goals**: sub-monthly freshness (would require live API + `list=recentchanges` deltas, not just dump exports); full revision history (the pipeline reads `mediawiki_content_current`, the current-state export, not `_history`); partial-transclusion coverage tracking at Page-namespace granularity (transcluded/untranscluded is tracked as binary per Index item, on purpose — see "Untranscluded Index items" above).
