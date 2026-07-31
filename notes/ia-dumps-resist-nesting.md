# IA-era dumps resist subpage nesting, inflating their text_count (open)

Opened 2026-07-31. Supersedes `backfill-text-count-discontinuity.md`, which
framed the problem backwards and should be read only as a record of what was
ruled out, not for its conclusions.

## The concept, stated correctly

`b579238` and `a63b21b` were written to **reduce spurious text counts** by
nesting pages that were being counted as standalone texts when they are really
chapters of a larger work. They succeeded: the whole `text_count` curve drops,
and the About page's y-axis rescales from ~7,000 to ~3,783 texts. That is the
intended effect and it is correct.

**The problem is that the reduction is uneven across source eras.** In
materialized (synthetic) months the nesting works and `text_count` falls to
roughly where it should be. In Internet Archive months roughly 1,110 pages
*fail to nest* and remain counted as top-level texts, leaving those months too
high relative to their neighbors.

The resulting chart shows notches. Read them correctly:

- **The notch bottoms are the materialized months** (2014-05, 2014-06, 2015-01,
  2015-05). These are approximately **right**.
- **The high plateau on either side is the IA months.** These are the ones
  still carrying spurious text counts — the fix is not reaching them.

So the defect is in the IA months, not the materialized ones. The fix direction
is to make nesting work on IA-era data so those months come *down* to the
materialized level. It is **not** to raise the materialized months back up.

This is worth stating flatly because it is the opposite of the intuitive read
("the synthetic data must be the broken one"), and an entire day was lost to
that assumption.

## How this was established

Bisected by rebuilding all 173 snapshots from the **unchanged** content caches
(route 3 — no network, no re-materialization) at each candidate commit, then
regenerating `changelog.json` and measuring the largest month-over-month
`text_count` swing. Target series extracted from the last known-smooth
committed changelog (`13dca6b`) for exact comparison.

| commit | what it changed | max month-over-month swing | vs target |
|---|---|---|---|
| `13dca6b` | (last smooth committed changelog) | 365 | — |
| `550dac9` | restore route-3 cache rebuilds | **365** | **173/173 months exact match** |
| `b579238` | nest subpages under nearest existing ancestor | 457 | 8/173 |
| `a63b21b` | nest महाभारतम् + ऋग्वेदः flat families | **1485** | 0/173 |

`550dac9` reproducing the target **exactly, on all 173 months**, is the load-
bearing result: it proves the content caches are intact, route 3 is faithful,
and nothing about archives, materialization, or fetching is involved. The
change is entirely in tree assembly.

### Commit-by-commit reference

Every commit on `dumpy-redo` after the last smooth changelog, in order, with
whether it is implicated. "changelog regenerated?" matters because the
committed `changelog.json` only changes when someone reran the backfill — a
commit can introduce a defect that doesn't appear in the committed file until a
*later* commit regenerates it. That is exactly what happened here.

| # | sha | date | what it did | changelog regenerated? | committed max swing |
|---|---|---|---|---|---|
| 1 | `13dca6b` | 07-29 | fix: current-month entry missing `all_stats` | yes | **365 — smooth** |
| 2 | `550dac9` | 07-29 | fix: restore cache-based (route 3) snapshot rebuilds; floor backfill at 2012-02 | no | — |
| 3 | `b579238` | 07-30 | fix: nest subpages under nearest existing ancestor | no | — |
| 4 | `a63b21b` | 07-30 | feat: nest महाभारतम् + ऋग्वेदः flat families | yes | **1485 — spiky** |
| 5 | `fc4d6c1` | 07-30 | fix: strip leftover HTML tags | no | — |
| 6 | `5c41f34` | 07-31 | feat: suppress duplicate flat listings | yes | 1485 (unchanged) |
| 7 | `53c5e17` | 07-31 | fix(data): upstream Viṣṇupurāṇa category fix | yes | 1485 (unchanged) |
| 8 | `bf65fde` | 07-31 | feat: source-type timeline in Data Quantity | no | — |

Ruled out, briefly:

- **#2 `550dac9`** — only changes *how* snapshots are rebuilt and which months
  run, not counting logic. Rebuilt output matches the target on all 173 months.
  It is also the earliest commit where route 3 works, which makes it the
  baseline every later test is run against. (`13dca6b` itself **cannot** be
  tested cheaply: route-3 cache rebuilds are broken there — that is the bug
  `550dac9` fixes — so rebuilding at `13dca6b` re-downloads and reparses raw
  dumps.)
- **#5 `fc4d6c1`** — byte counts only (`strip_markup`). Cannot touch
  `text_count`, which derives from page set, tags, redirects, and parenting.
- **#6 `5c41f34`** — *does* change counting (it suppresses page nodes), so it
  looks like a suspect a priori. Ruled out empirically: the changelog it
  produced has max swing 1485, identical to `a63b21b`'s. It did not move the
  numbers.
- **#7, #8** — data refresh and frontend only; numbers unchanged.

### When the spikes appeared, and how they grew

Rebuilt at each commit and measured. This is the actual progression:

| rebuilt at | max swing | 2015-01 (materialized) | 2015-02 (IA) | notch depth | chart |
|---|---|---|---|---|---|
| `550dac9` | 365 | 4,672 | 5,037 | ~365 | flat, no notches |
| `b579238` | 457 | 4,580 | 5,037 | ~457 | notches appear, shallow |
| `a63b21b` | 1485 | 1,250 | 2,735 | ~1,485 | notches at full height |

- **`550dac9` — no spikes.** 173/173 exact match to target. The curve sits at
  ~4,500–5,500 in 2014-15.
- **`b579238` — spikes appear.** Notches emerge at 2014-05, 2014-06, 2015-01,
  2015-05. IA months are unchanged from target (delta 0); only the materialized
  months move, dropping ~90. Shallow but structurally the final shape already.
- **`a63b21b` — spikes reach full height.** Both eras drop sharply, but
  unevenly: IA −2,302, materialized −3,412. The notch deepens to ~1,110, and a
  permanent step appears at 2017-08 where IA months also pick up the second
  family. This matches the committed spiky changelog exactly (1485), confirming
  the bisect.

The y-axis rescale between these runs is the intended effect and should not be
mistaken for the defect: at `550dac9` the 2026-07 endpoint reads ~7,125 texts;
at `a63b21b` it reads 3,783. The nesting work is *supposed* to cut the count
roughly in half. The defect is only that the cut lands unevenly by era.

### Per-era signature

Deltas against the smooth target, by era:

| | `b579238` | `a63b21b` |
|---|---|---|
| IA months | 0 | −2,302 |
| materialized months | −82 to −97 | −3,412 to −3,427 |
| **gap (= notch depth)** | **~90** | **~1,110** |

Both commits produce the same *kind* of defect at different scale. `b579238`
creates the notch structure (~90 pages); `a63b21b` deepens it to ~1,110 and
adds a permanent step at 2017-08.

The ~1,110 figure is close to the ऋग्वेदः family's 1,028 pages, and the
−2,302 figure is close to महाभारतम्'s 2,315. The working reading is that in IA
months **only महाभारतम् nests and ऋग्वेदः does not**, while in materialized
months both do. From 2017-08 onward IA months also reach −3,555 (both families
nested), which is why the high plateau ends there — that is a nesting change,
not a corpus event.

`_resolve_flat_family` never synthesizes a parent: if a destination page is not
found, its pages fall back to top-level. So a month where the destinations
aren't found produces exactly this — spuriously high `text_count`, with `count`
and byte totals untouched (they don't depend on tree position). That matches the
observation that Effective Size and Page Count stay smooth throughout.

## The open question

**Why do IA-era dumps fail to nest?** Not established. The candidate is that the
destination pages (`ऋग्वेदः मण्डल M`, the 18 `महाभारतम्/<parva>` pages) are
either absent from IA dumps, or present in a form our parsing doesn't resolve —
a namespace, redirect, or title-normalization difference specific to the classic
`pages-meta-current` format. Not yet checked directly.

The unchecked test: compare a materialized month against an adjacent IA month
and look for the destination titles in each one's content cache. Present in one
and missing in the other localizes it immediately.

## The decision this feeds

Whether to **keep IA as a data source at all**. If IA dumps are systematically
missing or mis-encoding page relationships, the nesting work has surfaced a real
defect in that source that was simply never visible before — every prior metric
(size, page count) is insensitive to tree structure, so nothing would have
caught it.

Planned experiment: **materialize the entire IA range** and see whether the
curve flattens. If it does, that is strong evidence the IA dumps themselves are
the problem rather than our reading of them, and the materialized path should
probably become the sole historical source. Note this is the expensive
`force_reprocess` path (~3-5 min/month plus materialization), not route 3.

Caveat worth holding: a flat curve after materializing everything would confirm
*consistency*, not correctness — every month would then share whatever biases
materialization has (notably the 80-vs-139 category-page shortfall in the cached
meta-history dump, and deviation #1's missing deleted pages). Consistency across
a series is nonetheless what the historical chart actually needs.

## Method note

Rebuilding snapshots at each candidate commit from the unchanged content caches
is cheap (seconds per month, no network) and gave an exact, unambiguous answer
where a full day of reasoning from code diffs produced repeated wrong
conclusions. For any "which commit changed this number" question in this repo,
do the rebuild first and skip the theorizing.
