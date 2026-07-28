# Plan: cache per-month content data so tree-logic fixes don't need a full backfill re-run

## Motivation

Confirmed this is a recurring pattern, not a one-off: several past fixes
have changed `pipeline/build_tree.py`/`pipeline/process.py`'s tree-assembly
or stat-rollup *logic* (e.g. `35489f1` subpage category divergence,
`2cfd9c8` redirect resolution in breadcrumb parenting + text_count,
`ca579df` transclusion undercount, `e23a863` own-vs-rollup stats, and this
session's redirect-stub-as-orphan fix, commit `5e0c6db`). Every one of
these changes historical counts the same way it changed the live tree, but
today the only way to propagate a logic fix into `docs/data/changelog.json`
is a full `pipeline/backfill.py` re-run across the whole historical range —
confirmed by the user to take **8-10 hours**.

## Why a full rebuild is currently required (confirmed by inspection)

`dump/_backfill_snapshots/tree-<date>.json` (153 files, back to 2012-02,
~3.1GB total on disk today) stores only the **final assembled tree** — e.g.
a `page` node has `title`/`url`/`stats`/`subpages`, nothing about whether
the underlying record was a redirect, what its raw category tags were, etc.
Confirmed via direct inspection: no `redirect_target` field or equivalent
anywhere in a snapshot. So a snapshot cannot be patched in place for a
tree-shape/logic bug — there's nothing in it to select the affected nodes
by; you'd have to re-derive the corrected shape from the original page
records, which the snapshot doesn't retain.

`pipeline/backfill.py`'s `ensure_snapshot` also short-circuits if
`tree-<date>.json` already exists (skips rebuilding it entirely on a
resumed run) — so today, picking up a `build_tree_json` fix effectively
requires deleting some or all existing snapshots first, then re-running
`run_backfill_sequence.sh`, which pays the full cost of `parse_dump` +
`build_tree`/`transclusion` + `compute_all_content_sizes` (the slow part)
all over again for every month, even though only the last, cheap step
(`build_tree_json`) actually needs to re-run for most fixes seen so far.

## Where the actual cost is (confirmed, this is the crux of the plan)

`pipeline/backfill.py`'s `_build_snapshot` (or process.py's `main`,
same call chain) does, per month:

1. `parse_dump` — parses the whole dump XML. Cheap-ish (single pass,
   O(1) memory), but proportional to dump size (up to ~1.8GB for the
   current era).
2. `build_main_tree` / `build_category_graph` / `build_transclusion_map` —
   all pure in-memory dict/graph construction from already-parsed page
   records' titles, category tags, and `<pages .../>` tags. Cheap.
3. `compute_all_content_sizes` (`pipeline/process.py`) — **the expensive
   step**: parses every page's wikitext with `mwparserfromhell`, expands
   templates by looking up and substituting matching Template pages,
   strips markup, and (if enabled) transliterates via `skrutable`. This is
   what `run_backfill_sequence.sh`'s 8-10 hours is actually mostly spent
   on, run 153 times.
4. `build_tree_json` — walks the category graph and main-page tree,
   assembles the final JSON shape, computes dedup'd stats rollups. Pure
   dict/graph work over already-computed sizes. **This is where nearly
   every fix listed above actually lived.** Cheap — seconds, not minutes,
   per month (it's the same order of cost as `build_category_graph`, not
   `compute_all_content_sizes`).

So: **the fixes that keep recurring are almost all in step 4, but paying
for a fix currently means re-running step 3 anyway, 153 times, because
step 3's output isn't kept anywhere.**

## Proposal

Cache a lightweight, JSON-serializable snapshot of exactly what
`build_tree_json` consumes as input, alongside (not instead of) today's
`tree-<date>.json` output snapshot. Call it e.g.
`dump/_backfill_content_cache/content-<date>.json`.

### What to store (measured, see below)

- `ContentIndex`'s four dict fields, but only the byte-count integers from
  each `ContentSizeResult` (NOT `stripped_text`/`transliterated_text` —
  those are large and `build_tree_json` never reads them, only
  `.raw_wikitext_bytes`/`.content_bytes`/`.transliterated_bytes`):
  - `main_sizes: {title: {raw, content, translit}}`
  - `index_sizes: {bare_title: {raw, content, translit}}`
  - `main_categories: {title: [cat, ...]}`
  - `index_categories: {bare_title: [cat, ...]}`
  - `index_timestamps: {bare_title: iso_timestamp}`
  - `index_page_rollup: {bare_title: {raw, content, translit}}` (already
    pre-summed stats dict, not a list — matches `Stats` shape)
- Whatever else `build_tree_json`'s other positional args need that isn't
  already cheap to rebuild from a fresh `parse_dump` pass over a *tiny*
  residual XML, OR is itself now worth caching for the same reason:
  - `redirect_target` per Main-namespace title (needed by
    `build_main_tree`'s `_resolve_redirect`, and by this session's
    redirect-stub fix in `build_tree_json` directly) — not currently in
    `ContentIndex` at all, would need adding explicitly.
  - Main-namespace page timestamps (`build_page_node`'s `last_changed`) —
    check whether these are already implicitly covered by keeping
    `main_sizes`, or need their own field. (Not yet confirmed — verify
    when implementing.)
  - Category-graph edges (`build_category_graph`'s parent/child sets) —
    small, cheap to rebuild from Category-namespace records alone, but
    those Category-namespace records themselves are also deleted with the
    raw dump today. **Open question**: either (a) keep Category-namespace
    page bodies specifically (they're a small fraction of the dump — ~250
    categories vs 160k+ Main pages, confirmed via `graph.nodes` count
    logged by `build_category_graph`'s `main()`), or (b) also cache the
    already-built edge list directly, same treatment as the other fields.
    (a) is probably simpler and more future-proof (survives NEW
    `build_category_graph`-level logic changes too, not just
    `build_tree_json`-level ones) — leaning towards this.

### Measured size (real number, not a guess)

Ran a one-off script against the current live dump (~165k pages, the
largest/most expensive month by far — most historical months are smaller):
the four `ContentIndex` fields (ints only, no text) plus a
`redirect_target` map serialize to **4.8MB uncompressed / 0.7MB gzipped**
as JSON. Compare to that same month's existing `tree-<date>.json` output
snapshot (~34MB) and its source dump XML (~1.8GB).

Both the new content cache AND the existing `tree-<date>.json` output
snapshot are now written gzip-compressed by default (implemented — see
"Implementation" below), so the actual per-month added cost is close to
the gzipped figure, not the uncompressed one: ~0.7MB per month at the
current/largest month, most historical months smaller. Across all 153
months, that's on the order of ~50-100MB gzipped added — and since
`tree-<date>.json` itself also now compresses (JSON with lots of repeated
key names gzips well, typically 5-8x), gzipping BOTH file types nets out to
a smaller `_backfill_snapshots/` + new cache dir combined footprint than
the ~3.1GB `_backfill_snapshots/` alone held before this change,
uncompressed.

(Category-namespace page bodies, cached per option (a) above, add very
little — on the order of a few hundred KB per month given ~250 categories
total.)

### How this changes the fix-and-rebuild workflow

For a `build_tree_json`-only bug fix (the common case so far):

1. For each month, load the cached content-cache JSON instead of
   re-running `parse_dump` + `compute_all_content_sizes` against the
   (now-deleted) raw dump.
2. Reconstruct the small in-memory shapes `build_tree_json` needs
   (`ContentIndex`, `main_nodes` via a cheap `build_main_tree`-equivalent
   over just title/redirect_target/category-tag data, `CategoryGraph` via
   cached category bodies or cached edges).
3. Re-run `build_tree_json` fresh (seconds).
4. Overwrite `tree-<date>.json` with the corrected output.
5. Re-run `pipeline.compare`'s pairwise diffs and rebuild
   `docs/data/changelog.json` from the corrected snapshots (cheap — it's
   just diffing already-built JSON trees, no dump parsing at all).

Only a genuine `compute_all_content_sizes`-level bug (rarer — e.g. a
byte-counting or transliteration defect) would still require the full,
slow rebuild, and only for months actually affected.

### Scope not yet decided / left for implementation time

- Exact schema/versioning for the cache file (e.g. a schema-version field
  so a future structural change to `ContentIndex` itself can detect stale
  caches and fall back to a full rebuild automatically, rather than
  silently miscomputing).
- Whether to build this cache retroactively for all 153 already-backfilled
  months (requires one full slow pass, but only once, ever, going
  forward) or only prospectively (cheap fixes only help months backfilled
  *after* this lands, until/unless the retroactive pass is also done).
- Whether `pipeline/backfill.py`'s resume logic (`ensure_snapshot`
  skip-if-exists check) should be taught to distinguish "snapshot is
  current" from "content cache exists but tree needs rebuilding from it" —
  probably a new function alongside `ensure_snapshot`, e.g.
  `rebuild_tree_from_cache(date_str)`, invoked by a new `backfill.py` CLI
  mode (e.g. `--rebuild-trees-only`) separate from the normal fetch-and-
  build path.
- `pipeline/compare.py`/`docs/data/changelog.json` regeneration after a
  tree-only rebuild pass — confirm `build_report` only reads the two
  snapshot JSONs (should be true, not yet re-verified) so this step really
  is cheap and needs no dump/cache access at all.

## Status

**Implemented.** Summary of what landed (differs from the plan sketch above
in a few small ways, noted inline):

- `pipeline/snapshot_io.py` (new): `write_json_gz`/`read_json_gz`/
  `read_json_maybe_gz` — shared gzip-transparent JSON I/O used by both the
  content cache and the tree snapshots.
- `pipeline/content_cache.py` (new): `build_content_cache` (called right
  after `compute_all_content_sizes`, while everything's still in memory) and
  `rebuild_inputs_from_cache` (reconstructs `ContentIndex`, `CategoryGraph`,
  `main_nodes`, and both transclusion maps purely from a cached
  `content-<date>.json.gz`, no dump XML involved). Includes a
  `schema_version` field (int, bumped on any structural change to the cache
  shape) so a stale cache from before a future format change fails loudly
  (`ValueError`) instead of silently miscomputing.
  - Followed option (a) for the category graph: caches raw Category-namespace
    page bodies (`{title: wikitext}`, ~250 entries) rather than pre-built
    edges, so a future `build_category_graph`-level fix (not just
    `build_tree_json`-level) can also replay from the same cache.
  - Transclusion detection: rather than caching raw Main-page wikitext (which
    the plan's own point was to avoid — that's the large stuff), each Main
    page's cache entry stores the small *derived* result
    (`transcluded_index_titles`, a regex scan) directly, alongside
    `redirect_target` and `timestamp`. `_augment_main_sizes_with_transclusion`
    (the `<pages from=/to=/>` byte-folding step) already ran before caching,
    so its output is baked into the cached `main_sizes` — no need to redo it
    on rebuild.
- `pipeline/backfill.py`: `process_dump` now returns `(tree, content_cache)`.
  `ensure_snapshot` writes both `tree-<date>.json.gz` and
  `content-<date>.json.gz`. New `rebuild_tree_from_cache(date_str, ...)` does
  the cheap-path rebuild (cache → `build_tree_json` → overwrite snapshot,
  seconds not minutes). New `--rebuild-trees-only` CLI mode (implements the
  "Scope not yet decided" question below in the affirmative): rebuilds trees
  for `--months` (default: every month with an existing cache), then
  recomputes every consecutive pair's diff and **overwrites in place** any
  existing `docs/data/changelog.json` entry for that exact old/new date pair
  (matched by `(old_date, date)`, same `id` preserved) — deliberately does
  NOT skip-if-exists like a normal backfill run, since the whole point is
  correcting stats already logged. Pairs with no existing changelog entry are
  left alone (rebuilding trees doesn't invent new changelog transitions).
  `_existing_snapshot_path` recognizes either `tree-<date>.json.gz` (new
  default) or the older bare `tree-<date>.json` (already-backfilled months
  from before this change) as "already built," so old snapshots aren't
  silently reprocessed.
- `pipeline/compare.py`: `build_report` reads via `read_json_maybe_gz`, so it
  transparently diffs any mix of gzipped and legacy plain-JSON snapshots.
- `pipeline/validate_materialization.py`: updated for `process_dump`'s new
  tuple return and gzip-aware snapshot lookup (`_existing_snapshot_path`
  instead of a hardcoded `.json` path).
- Verified via a synthetic round-trip test (small hand-built `DumpIndex`
  exercising a redirect chain, a multi-parent category, and
  ProofreadPage transclusion): the tree built by the cache round-trip
  (`build_content_cache` → gzip → `load_content_cache` →
  `rebuild_inputs_from_cache` → `build_tree_json`) is byte-for-byte identical
  to the tree built by the direct path. Also verified `--rebuild-trees-only`
  end-to-end against a fake changelog entry seeded with wrong stats — the run
  overwrote it in place with the correct recomputed diff, same `id`.

Not yet done (still real work, left for whenever the fix that motivates it
actually shows up):
- **Retroactive cache build for the existing 153 already-backfilled months.**
  They only have `tree-<date>.json` (uncompressed, no `.json.gz`, no content
  cache) until a full backfill re-run passes over them again. Nothing forces
  that re-run now — it only needs to happen once, whenever the next
  `build_tree_json`-level fix actually lands and someone wants to propagate
  it cheaply. Until then, `--rebuild-trees-only` simply has nothing to do for
  those months (skipped with a warning, per `rebuild_tree_from_cache`'s
  `FileNotFoundError` handling).
- Re-verify `pipeline/validate_materialization.py`'s comparison logic end-to-
  end against a real (non-synthetic) dump pair, since only the synthetic
  round-trip test above has actually run so far.
