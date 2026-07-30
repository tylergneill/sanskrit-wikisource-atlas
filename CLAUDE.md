# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A more accessible browsing interface for the Sanskrit text collection at sa.wikisource.org. Wikisource's category structure is hard to browse (no good overview for non-technical users, disorienting subcategory nesting, no metadata like filesize, no transliteration). This project builds `docs/data/tree.json` from Wikimedia's monthly XML dump exports and renders it as a static, searchable, transliteration-aware site published to GitHub Pages from `docs/`. It also maintains a historical changelog of how the corpus has grown over time, rendered on the About page.

## Architecture

Three parts connected by generated JSON files:

1. **Pipeline** (`pipeline/`) — a multi-stage Python pipeline, run stage by stage via the `Makefile` targets below, that turns a downloaded MediaWiki XML dump into `docs/data/tree.json`:
   - **Fetch** (`pipeline/fetch.py`) — locates, downloads, verifies, and decompresses the current monthly Content File Export for sa.wikisource.org from `dumps.wikimedia.org/other/mediawiki_content_current/`. Only a 3-month rolling window is available at this endpoint. Discovery has no API: generation starts monthly on the 1st, and a run is only complete once `SHA256SUMS` appears alongside it, so `find_latest_export` checks candidate month directories for that file's presence rather than trusting a listing. The single XML export covers every namespace the pipeline needs (Main, Category, Index, Page, Template, Module) in one download.
   - **Parse** (`pipeline/parse_dump.py`) — stream-parses the dump XML (`iterparse`, one `<page>` at a time, O(1) memory) into per-namespace page records (`DumpIndex`).
   - **Build tree** (`pipeline/build_tree.py`) — constructs the Main-namespace subpage tree (pure tree, split on `/` — a page's parent is the *nearest existing ancestor path*, redirect-resolved at every level, see "Subpage parenting" below) and the Category digraph (manually-maintained, not guaranteed acyclic or fully connected — see "Multi-parented categories" below).
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
make backfill               # walk the full historical range, rebuild docs/data/changelog.json from scratch
make regen-changelog         # rebuild docs/data/changelog.json from already-cached snapshots only, no network access
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

### Subpage parenting (`build_main_tree` / `_resolve_ancestor`)

A Main page's parent is **the nearest ancestor path that actually exists as a page**, not simply "everything before the last `/`". `_resolve_ancestor` tries, in order: the exact literal immediate parent; then each higher ancestor, longest-first; and at every level it both redirect-resolves the candidate and allows a whitespace-normalized match (`_normalize_path` strips spaces around each `/` segment — sa.wikisource has real titles like `अब्धिनौयानमीमांसा /चतुर्थं खण्डम्`, which MediaWiki itself normalizes when resolving subpages on the live wiki). If nothing resolves, the page stays top-level — a parent is **never synthesized**, and those genuinely-stranded pages are reported by `pipeline.audit`'s `find_unresolvable_slash_paths` instead.

Three constraints worth preserving if this is ever touched again:

- **Exact-literal-first is load-bearing, not an optimization.** `भविष्यपुराणम् /पर्व १ (ब्राह्मपर्व)` is itself a real page whose *own title* carries a stray space, with 226 chapters nesting under it by exact match. Normalizing the path before trying it verbatim would miss, fall through to the shallow root, and pull all of them up a level. There are 73 such normalized-key collisions, which is why `titles_by_normalized` maps to a *list* of real titles rather than one.
- **Redirect resolution happens at every level, not just the immediate parent.** `श्रीमद्भागवत महापुराण/स्कंध ०१/अध्यायः ०१` only reaches its real home by resolving the *root* segment's redirect to `श्रीमद्भागवतपुराणम्`.
- **A candidate resolving to the title itself is rejected.** `कथासरित्सागरः/लम्बकः १३` is a redirect pointing *down* at its own child, which otherwise makes that child its own parent — it then belongs to no root and disappears from the tree entirely (it was absent from a shipped `tree.json` for exactly this reason).

Because `parent_title is None` is simultaneously the predicate for `text_count`, orphan-bucket eligibility, the "parent already carries this tag" filing suppression, and the audit's candidate pool, a parenting miss inflates the text count, pollutes the orphan bucket, *and* corrupts the audit's input at once. Deliberately **not** inferred: non-`/` separators (`महाभारतम्-03-आरण्यकपर्व-001`), since a hyphen carries no structural meaning on MediaWiki — those stay flat and are reported by `find_separator_family_candidates` for a human to fix upstream with a page move. See `notes/wikisource-editing-plan.md`.

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
3. **Materialized era** (`MATERIALIZED_MONTHS`, `_ensure_materialized_month`) — covers every interior hole in the two legacy sources' combined coverage via the same on-demand mechanism. `compute_materialized_months()` scans `fetch_legacy.list_available_months()` for any `YYYY-MM` with no entry, strictly between the earliest and latest months either source covers, at or after `MATERIALIZED_FLOOR` (2012-02 — `वर्गसर्वस्वम्`'s earliest revision is 2012-01-20T10:18:19Z, so a 2012-01-01 cutoff predates the root category by 19 days and can only raise `RootCategoryMissing`; 2012-02-01 is the first cutoff that lands after it. See `RootCategoryMissing` below for months before that). The floor is also applied in `default_months()`, not just here: Internet Archive genuinely has pre-2012 dumps (2011-09, 2011-10), which reach the month list as real dumps rather than materialized holes, and without the floor were fetched and parsed in full only to be skipped. This isn't a hardcoded list of ranges; it's detected live against `fetch_legacy`'s two sources (itself disk-cached, 24h TTL) each time `pipeline.backfill` is imported. As of this writing it finds six holes (91 months total): 2012-02 through 2014-06 (Internet Archive's last dump before `वर्गसर्वस्वम्` existed was 2011-10-13; its first dump after real legacy coverage resumes is 2014-07-01), 2015-01, 2015-05, 2018-04 through 2018-07, 2019-04 through 2020-06, and 2022-06 through 2025-10 (the gap between Internet Archive's last snapshot and the live rolling window's start).

   No dump file exists for these months on any source, so `pipeline/materialize_snapshots.py` reconstructs one on demand from `sawikisource-latest-pages-meta-history.xml.bz2` (every surviving revision ever made): for a cutoff date D, a page's state is the newest revision with timestamp ≤ D. Each month's reconstruction is generated on demand, one at a time, right when `ensure_month` needs it, and its raw XML is deleted again immediately after its snapshot is written — never more than one materialized dump on disk at a time. The underlying meta-history dump itself (~533MB) is downloaded once and cached, since re-downloading it is the expensive part. See `pipeline/materialize_snapshots.py`'s docstring for known deviations from a genuine dump of that month, and `pipeline/validate_materialization.py` (kept in the repo for future re-validation, not run automatically) for accuracy validation against real dumps at the era boundaries — confirmed within ~0.5-0.6% on every metric for the 2022-2025 gap; re-run against other era boundaries (2014-07, 2018-03/2018-08, 2019-03/2020-07) before trusting those ranges equally.

   **Dump vintage.** Every materialized month inherits the vintage of whichever meta-history run happens to be cached, and nothing in the code or its outputs records which one that is: `MATERIALIZE_SOURCE_URL` fetches the undated `latest/` alias, the file is saved under that same undated name, and `_ensure_materialize_source` is `if dest.exists(): return dest` — no freshness check, by design (`cleanup_raw_dump` never touches it). So the vintage has to be derived from the newest `<timestamp>` in the 6.5GB decompressed file, and no tree snapshot, `changelog.json` entry, or `source_eras.json` field carries it. **The currently cached dump is the 2026-07-01 run** (newest revision 2026-07-02, since runs cut at run time rather than midnight; `<generator>MediaWiki 1.46.0-wmf.26</generator>`) — established in `notes/pre-2012/pre-2012-corpus-history.md`'s "Files" section, which is also where the category-free stats path used to validate this method byte-for-byte against `changelog.json` lives. Two consequences: `materialize_snapshots.py`'s deviation #1 (pages deleted before the dump was taken are absent) makes every materialized month a **lower bound** as of that date, and the ~0.5-0.6% validation figure above was measured against that specific vintage. Refreshing the cached dump therefore invalidates all 91 materialized months (the six ranges above, as currently detected) — deliberately re-download it only if the deleted-page drift matters more than reproducibility, then delete `dump/_backfill_snapshots/tree-<date>.json.gz` for every materialized month, re-run scoped `pipeline.backfill --months` walks (~3-5 min each), `make regen-changelog`, and re-note the new vintage here.

For each month, `ensure_month` resolves an exact date to the right era (materialized months checked first, since the other two sources are confirmed to never have them). Raw dumps land in one of four numbered era folders under `dump/`, each holding its own dated subdirectories — `1_current_format_live/<date>/` (era 1, routine `make process`'s live dump — never touched by backfill), `2_legacy_format_live/<date>/` (era 2), `3_materialized/<date>/` (era 3, every `MATERIALIZED_MONTHS` date regardless of which hole it's in), `4_legacy_format_archive/<date>/` (era 4) — see `pipeline/backfill.py`'s `DEFAULT_*_ROOT` constants. Once a month's snapshot is written, its raw dump directory is deleted immediately (`cleanup_raw_dump`) — pass `--keep-raw-dumps` to disable this.

**Genuinely too-early dumps**: sa.wikisource's category system, and later the ProofreadPage extension (Index/Page namespaces), didn't always exist. The oldest available Internet Archive dump (2011-10-13) predates both — confirmed only 3 categories existed on the entire site at that point, none of them `वर्गसर्वस्वम्`. `parse_dump.py`'s `index_ns_id()`/`page_ns_id()` return `None` (not an error) when those namespaces are genuinely absent from a dump's siteinfo, and `process_dump()` raises a distinct `RootCategoryMissing` when the root category itself doesn't exist yet, which `backfill.py`'s `main()` catches and skips (logging a note) rather than aborting the whole run.

`pipeline/run_backfill_sequence.sh` drives `pipeline.backfill` one month-pair at a time (so a failure on one pair doesn't lose earlier progress), starting from the newest anchored current-era month and walking backward through every legacy + materialized month. Deletes and rebuilds `docs/data/changelog.json` from scratch on every run (see below). Safe to interrupt and rerun — already-fetched/materialized dumps and already-built snapshots are skipped/reused, not redone; the changelog itself is always fully rebuilt, which is cheap.

`pipeline/backfill.py` deliberately does NOT write `docs/data/tree.json` or `docs/VERSION` — those reflect the live, current-month pipeline state, not a historical replay.

`docs/data/source_eras.json` (read by `about.html`'s Snapshots section, to describe era 1/era 2's current live-rolling-window start dates) is refreshed by a separate module, `pipeline/update_source_eras.py`, not by `pipeline.backfill` itself — it does two live network lookups (~1 minute total) that have nothing to do with any particular month-pair, so folding it into every `pipeline.backfill` invocation would mean paying that cost on every one of `run_backfill_sequence.sh`'s 150+ per-step calls for no reason. `run_backfill_sequence.sh` runs it once, standalone, after its whole walk finishes.

`pipeline/fetch_legacy.py`'s `list_available_months()` (merged live-rolling-window + Internet Archive month listing, used by `_ensure_legacy_month`, `default_months()`, `update_source_eras`, and `run_backfill_sequence.sh`'s own upfront `--list` call) is genuinely expensive — 2 listing requests plus one more request *per date* in each listing, dozens total, not just 2. Since `run_backfill_sequence.sh` spawns a fresh `python -m pipeline.backfill` subprocess per step, an in-memory cache wouldn't help; it's cached to disk instead (`dump/_fetch_legacy_months_cache.json`, 24h TTL — see `LIST_AVAILABLE_MONTHS_CACHE`/`LIST_AVAILABLE_MONTHS_CACHE_TTL`), so every caller across an entire `run_backfill_sequence.sh` walk shares one query instead of re-deriving the identical listing on every step. Pass `use_cache=False` (or `fetch_legacy --list --no-cache`) to force a fresh query.

### Two on-disk layers per month, and what deleting each one triggers

For each backfilled month, `ensure_snapshot` writes two separate gitignored, gzipped files under `dump/`, plus one shared, git-tracked output:

- **`dump/_backfill_content_cache/content-<date>.json.gz`** (the *input* layer) — the small, cheap-to-derive-but-annoying-to-lose inputs `build_tree_json` needs: per-page byte counts (raw/content/transliterated — the output of `compute_all_content_sizes`, the genuinely slow step: `mwparserfromhell` parsing, template expansion, `skrutable` transliteration), category tags, redirect targets, timestamps, and transclusion results. See `pipeline/content_cache.py`.
- **`dump/_backfill_snapshots/tree-<date>.json.gz`** (the *output* layer) — the fully assembled `tree.json`-shaped snapshot, same schema as `docs/data/tree.json`. What `pipeline/compare.py` actually diffs pairwise.
- **`docs/data/changelog.json`** (git-tracked, not gitignored) — the append-only pairwise diffs between consecutive snapshots, keyed by `(old_date, date)`. This is the only one of the three that's committed and deployed.

`ensure_snapshot` skips a month entirely (both the cache and the tree snapshot) if `tree-<date>.json[.gz]` already exists. `docs/data/changelog.json` itself is deleted at the start of every `run_backfill_sequence.sh` run and rebuilt from scratch: `pipeline.backfill`'s `main()` always recomputes and overwrites (not skips) the changelog entry for every consecutive snapshot pair it sees, matched by `(old_date, date)` — cheap, since it's just a diff of two already-cached snapshots, no XML parsing — so every run reflects the current tree-assembly logic (`build_tree_json`/`build_category_graph`), never a stale entry left over from before a rollup/dedup/assembly fix (e.g. the redirect-parenting or subpage-category-divergence fixes, or the orphan-bucket `all_stats` fix). `id`s are stable across a rerun as long as the changelog isn't deleted mid-sequence by hand; deleting it (as `run_backfill_sequence.sh` does up front) does reset `next_id` to 1 and renumber every entry on that walk — harmless for display, since the changelog viewer sorts by `date`, not `id`.

Deleting `dump/_backfill_snapshots/tree-<date>.json.gz` for a specific month forces a full reprocess of that month on the next `pipeline.backfill` run (plain, or via `run_backfill_sequence.sh`) — re-fetches and re-parses the dump from scratch, including `compute_all_content_sizes` (the slow step: `mwparserfromhell` parsing, template expansion, `skrutable` transliteration), even though `dump/_backfill_content_cache/content-<date>.json.gz` may still hold valid cached inputs for that exact month. There's no fast-path flag that consults the content cache instead — `ensure_snapshot` only ever checks whether the tree snapshot exists, not whether cheaper inputs are available to rebuild it from. To fix a specific month, scope with `--months`: `python -m pipeline.backfill --months OLDER NEWER` (cost is ~3-5 min for the one deleted month; every other requested month whose snapshot still exists is reused instantly) rather than a full `make backfill` walk, unless you actually want the full historical re-derivation.

Deleting `dump/_backfill_content_cache/content-<date>.json.gz` alone (without also deleting the tree snapshot) has no effect on the next run, since `ensure_snapshot` never re-derives a snapshot that already exists — the cache is only read when a snapshot is actually being rebuilt (i.e. also deleted).

In short: **`make backfill` always redoes the changelog** (deleted and rebuilt every run); **to redo a tree snapshot for a specific month, delete it and rerun scoped to that month** — there's no cache-only fast path anymore, so this always costs a real re-fetch/re-parse for that month.

`make regen-changelog` is a narrower, fully offline variant of the same rebuild: it lists whatever dates already have a snapshot under `dump/_backfill_snapshots/` (no network calls — not even `fetch_legacy.list_available_months()`) and passes exactly those as `--months` to `pipeline.backfill`, so every month is an instant snapshot-reuse and the whole run is just re-diffing already-cached snapshots (well under a minute for the full range, as of this writing). Use it instead of `make backfill` whenever the snapshots themselves are already trusted and only `pipeline/compare.py`'s diffing logic (or something in `all_stats`/`build_tree_json`, if those snapshots already reflect the fix) needs to be picked up in the changelog — e.g. how this repo's `असम्बद्धवर्गीकृतम्` orphan-bucket trend-chart dips got fixed. It does not fetch, materialize, or rebuild any snapshot — if a month's snapshot is missing or wrong, it's silently excluded from the diff sequence (or diffed with wrong data) rather than fixed; use `make backfill` (or a scoped `python -m pipeline.backfill --months`) for that.

## Notes

- `docs/VERSION` holds `__code_version__` (bump manually on user-visible frontend changes), `__data_version__` (pipeline-run date, stamped automatically by `process.py`'s `_stamp_data_version`), and `__content_version__` (the Wikimedia dump export's own date, also stamped automatically) — three separate dates, since a pipeline run's date and the dump's own snapshot date can differ.
- `notes/` holds prototype/spec material not yet absorbed into the maintained codebase, and one-off historical analysis scripts kept for the record (not meant to be re-run routinely).
- **Deliberate non-goals**: sub-monthly freshness (would require live API + `list=recentchanges` deltas, not just dump exports); full revision history (the pipeline reads `mediawiki_content_current`, the current-state export, not `_history`); partial-transclusion coverage tracking at Page-namespace granularity (transcluded/untranscluded is tracked as binary per Index item, on purpose — see "Untranscluded Index items" above).
