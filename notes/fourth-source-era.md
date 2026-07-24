# The fourth source era: a real, fillable gap between 2012-01 and 2014-07

CLAUDE.md's "Historical backfill and the changelog" section documents three
source eras for `docs/data/changelog.json` (current/live rolling window,
legacy/Internet Archive + live legacy window, materialized 2022-06→2025-10).
This note proposes a **fourth source era** -- materialized 2012-01→2014-06,
the gap immediately preceding the existing legacy era's earliest real
coverage -- using the same on-demand materialization mechanism already
built for the third era, just pointed at an earlier date range.

Investigated 2026-07-24, prompted by two observations after a full backward
`make backfill` run:

1. The walk's last two steps (`2011-10-01 -> 2014-07-01` and
   `2011-09-01 -> 2011-10-01`) fetch real Internet Archive dumps for
   2011-09-04 and 2011-10-13, both of which hit `RootCategoryMissing`
   (वर्गसर्वस्वम् doesn't exist yet) and get skipped -- confirmed via a
   direct rerun of just those two months: 2011-10-13 had 4,776 pages,
   2011-09-04 had 3,568, both cleanly logged "too early... skipping" with
   no crash.
2. Neither month shows up in the About page's Deltas section -- expected,
   since a skipped month produces no snapshot and therefore no pairwise
   comparison to log. Not a bug on its own; see finding below for what
   *would* actually change this.

## Are 2011-09/2011-10 themselves suspect? No -- confirmed legitimate

Queried Internet Archive directly (`fetch_legacy.list_archive_snapshots()`):
**only 119 sawikisource snapshots exist in total, ever, and only these
exact 2 fall before 2014-07.** There is no missing coverage on our end --
Internet Archive itself has no sawikisource dumps at all between late 2011
and mid-2014-07 (own real ~2.5-year hole in the volunteer archival record,
not a fetch failure or a query bug in this pipeline). These two months cost
under a minute total to download/parse/skip on every full backward run --
cheap, correctly handled, not worth excluding from the walk.

## The real finding: वर्गसर्वस्वम् existed from 2012-01-20 -- long before our first usable snapshot (2014-07-01), motivating a fourth source era

Checked the cached `sawikisource-latest-pages-meta-history.xml` (already
downloaded locally for the existing 2022-06/2025-10 materialization gap,
`dump/_materialize_src/`, ~6.5GB uncompressed) directly for
वर्गः:वर्गसर्वस्वम्'s revision history: **its earliest revision is
timestamped `2012-01-20T10:18:19Z`.**

This means the category system this whole mirror's tree model depends on
was already up and running by January 2012 -- a full **2.5 years** before
`docs/data/changelog.json`'s current earliest entry (2014-07-01). The
2011-09/2011-10 skip is correctly explained by `RootCategoryMissing` (the
category genuinely didn't exist then), but the 2012-01 → 2014-06 span is
NOT a "too early, nothing there" situation -- it's a **genuine, currently-
unfilled gap**, purely an artifact of:

- Internet Archive has no sawikisource dumps in this range (confirmed
  above -- the archival gap runs 2011-10 through 2014-06 inclusive, not
  just through the already-known 2022-05 → 2025-10 gap `MATERIALIZED_MONTHS`
  currently covers).
- `pipeline/backfill.py`'s `MATERIALIZED_START = "2022-06-01"` is hardcoded
  to the *third* source era (the already-known Internet-Archive-stalled to
  live-rolling-window-start gap) and doesn't currently extend backward to
  also cover this earlier 2012-01–2014-06 span -- a fourth, separate gap --
  even though the exact same materialization mechanism
  (`materialize_snapshots.py`, "for cutoff date D, take each page's newest
  revision <= D") would work here too -- the same meta-history dump already
  contains every revision back to the wiki's actual beginning, not just
  back to 2022.

## Why this matters

Currently the changelog silently treats "no IA dump exists" as equivalent
to "nothing meaningful happened here" for the ENTIRE pre-2014-07 span --
but that's only true for the ~4 months where वर्गसर्वस्वम् genuinely didn't
exist (roughly through late 2011/very early 2012). For the following 2.5
years (2012-01 through 2014-06), the site had a real, working category
structure and real content growth that the changelog currently shows
nothing for -- a real historical undercount in "Data Quantity: Historical
Trends and Deltas," not a cosmetic gap.

## Decision (2026-07-24): pursue the fourth source era, bounded at 2012-01

User confirmed the framing and wants to proceed: materialize back to
**2012-01** (recovering the full ~2.5-year gap where वर्गसर्वस्वम् already
existed), and stop there. Before 2012-01, वर्गसर्वस्वम् doesn't exist at
all, so continuing to root the tree from it stops working -- reaching
further back would require a fundamentally different, non-category-rooted
parsing strategy (e.g. some other structural anchor entirely, or accepting
a category-less tree shape for that era). Not impossible, but explicitly
judged not worth it right now -- 2012-01 is the deliberate stopping point
for this effort, not a placeholder to revisit immediately after.

## Fix direction (not yet implemented -- for a future session)

1. Add a fourth source era: extend `MATERIALIZED_MONTHS` (or add a second,
   separate materialized range/constant, e.g. `FOURTH_ERA_START`/
   `FOURTH_ERA_END`, keeping the existing third-era 2022-2025 range
   distinct rather than silently merging two different gaps into one name)
   to also cover 2012-01 through 2014-06, using the same
   `_ensure_materialized_month`/`materialize_snapshots.py` mechanism
   already built for the third era -- no new reconstruction code needed,
   just a wider date range fed to the same function.
2. Verify wherever वर्गसर्वस्वम् is close to its creation boundary
   (2012-01-20) that the category graph is meaningfully populated by
   whatever the first chosen cutoff date is (e.g. 2012-02-01) -- a
   snapshot taken too soon after the category's creation might still have
   very little filed under it, which is fine (real early history, not a
   bug) but worth eyeballing once implemented rather than assuming.
3. `pipeline/validate_materialization.py` already exists for spot-checking
   materialized-era accuracy against real dumps at era boundaries
   (confirmed within ~0.5-0.6% for the existing 2022-2025 range per
   CLAUDE.md) -- rerun the same validation approach against this new
   earlier range's boundary (2014-07, where a real Internet Archive dump
   already exists to compare against) before trusting it.
4. Per [[feedback_backfill_newest_first]] (standing project rule): this
   would be new territory for the *backward* walk to cover (older months,
   which is the correct direction) -- NOT a forward-catchup change, so it
   doesn't conflict with that rule. Should be implemented as an extension
   of the existing backward-only materialization mechanism, not a new
   automation path.

Not yet implemented. No code changed as part of this note -- purely
investigative, to be picked up in a future session.
