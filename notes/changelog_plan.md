# Data changelog: what it's for and how entries get made

## Purpose

Track how the mirror's numbers (distinct bytes/pages, last-edited dates)
change over time, and make that history public via `docs/about.html`. The
motivating problem: the numbers can move for several different reasons at
once (live Wikisource content changing, scraper bugs getting fixed, scraper
features being added), and conflating them in one diff makes the history
misleading. Each changelog entry should isolate one cause.

## Storage and rendering

- `docs/data/changelog.json` — append-only array of entries, each with a
  flat shape: `id` (sequential integer, order of entries), `date`, `label`
  (short human summary of what's being isolated), `notes` (longer free-text
  explanation), `old`/`new` (`{bytes, count, last_changed}`, read directly
  from each side's `root.stats` — trusted as-is, not recomputed), `delta`
  (`{bytes, bytes_pct, count, count_pct}`), and page-level detail
  (`pages_added`, `pages_removed`, `pages_with_changed_timestamp`).
- `docs/about.html` + `docs/about.js` render it, newest first, each entry as
  a card showing `#<id> — <date> (<label>)`, the notes, the headline
  bytes/count delta, and a collapsible page-level detail list.
- `docs/index.html`'s topbar link "Source Root Page" + "GitHub" were
  replaced with a single "About" link to this page.

## `compare.py`: the steady-state tool, not a general-purpose one

`compare.py OLD.json NEW.json [--append --label ... --notes ...]` diffs two
`tree.json` files' `root.stats` directly (bytes/count/last_changed) plus
page-level added/removed/retimestamped detail, and can append the result as
a new changelog entry (auto-assigning the next `id`).

**Deliberately simple, trusts `root.stats` as-is, no independent
recomputation.** This is safe *only* because current `scrape.py`'s
`attach_stats()` already dedupes by page id when computing `root.stats` (see
CLAUDE.md's "Multi-parented categories" section) — so on any file produced
by the current pipeline, `root.stats` is correct by construction, and
`compare.py` doesn't need to re-derive it independently.

**This tool is for comparisons where both sides come from the same pipeline
feature-set** — i.e., true apples-to-apples runs, like "rerun the scraper
today vs. last week" once the pipeline is stable. It is NOT meant to handle
transitions where the *scraper itself* changed what it measures or how
(e.g. gaining subpage descent, or predating the dedup fix). Comparisons that
cross such a transition are one-offs (see below) — `compare.py` should not
grow special-case flags/logic to accommodate them. An earlier draft of this
tool had `compare.py` always independently recompute distinct-page totals
(a "reported vs. recomputed" split) specifically so it could also audit
`main`'s pre-fix data — that design was reverted per user feedback: it's
scope creep for a tool meant to serve the common case, and the audit need
only ever exists once, for `main`, not as a permanent tool feature.

## One-off comparisons (not `compare.py` runs)

Comparisons that cross a change in what the pipeline measures need custom,
throwaway scripts — written once, run once, output appended to
`changelog.json` in the same flat shape `compare.py` would produce (so the
frontend renders them uniformly), then discarded. `compare.py` itself is
never modified to accommodate them.

**Why one-offs instead of extending `compare.py`:** each of these
transitions involves a snapshot whose `root.stats` isn't a fair, direct
data point to diff against — either because it's outright wrong (main,
pre-dedup-fix) or because comparing it naively would conflate two distinct
effects (bug fix + live-data drift in a single diff). Baking handling for
these cases into `compare.py` would mean permanently carrying complexity
that's only ever needed for a handful of historical entries.

### Entries produced so far

- **#1 — bug-fix effect only, on unchanged data.** `main`'s branch tip
  tree.json predates the multi-parent-category dedup fix (see CLAUDE.md),
  so its own reported `root.stats` (273,693,514 bytes / 4,743 pages)
  double-counted 130 pages filed under 2+ categories. This entry's "old" is
  that buggy self-reported number; "new" is the *same* `main` data with
  today's dedup-by-page-id logic manually applied (259,163,815 bytes /
  4,522 pages) — i.e., same content, two different counting methods, zero
  live-data change. Produced by a throwaway script
  (`oneoff_main_dedup_audit.py`, not checked into the repo — lived in the
  session scratchpad), not `compare.py`.

- **#2 — live-data growth only, clean-to-clean.** "old" is #1's
  recomputed/clean baseline (259,163,815 bytes / 4,522 pages) — not
  `main`'s own buggy `root.stats`. "new" is the current branch tip's
  `root.stats` (293,465,139 bytes / 4,625 pages), which is already
  dedup-correct. Both sides deduped, so the delta (+13.2% bytes, +2.3%
  pages, 107 pages added / 4 removed) isolates pure live-Wikisource-content
  growth between the `main` snapshot and today, with the bug-fix effect
  already factored out in #1. Page-level last-edited-date diffing isn't
  possible against `main` (predates that field entirely) — noted explicitly
  in the entry so "0 pages with a changed timestamp" isn't misread as
  "confirmed no edits." Also a throwaway script
  (`oneoff_main_clean_vs_tip.py`), not `compare.py`.

### Anticipated future one-off

- **Subpage descent** (`notes/pipeline_upgrade_plan.md` item 2, currently
  gated off by `--recurse-subpages`, default off): once enabled by default,
  comparing pre-subpage-descent tip vs post-subpage-descent tip will show a
  large jump in page count/bytes that is *real newly-discovered content*
  (e.g. an index/ToC page expanding into dozens of real subpage children —
  see CLAUDE.md's नैषधीयचरितम् case study), not a bug or duplication. This
  transition doesn't need new code in `compare.py` either — the diff
  machinery (added/removed page ids, byte delta) will produce accurate
  numbers on its own. What it needs is a clear one-time note on that
  specific changelog entry distinguishing "newly discovered real content"
  from "double-counting," so a public reader doesn't mistake the growth for
  another data-quality bug. After that entry, further scrapes under the
  now-stable (subpage-descent-enabled) pipeline go back to being ordinary
  `compare.py` runs.

## Data-updated datestamp (separate from the changelog itself, but related)

`docs/VERSION` gained a second line, `__data_version__ = "YYYY-MM-DD"`,
stamped automatically by `scrape.py` (`_stamp_data_version()`, called at the
end of `main()`) every time the scraper runs — distinct from `__version__`
(code version, bumped manually, per existing user preference: [[feedback_no_version_bump]]
still applies to `__version__` specifically, not this new field). The
frontend topbar (`docs/index.html` / `docs/app.js`) shows this next to the
code version as "data updated YYYY-MM-DD", answering "when did the pipeline
last actually run" — deliberately NOT the same thing as `root.stats.last_changed`,
which is the max edit date across mirrored *Wikisource content* (a property
of the source data, not of when we last scraped it).
