# Backfilled text_count has sharp discontinuities at materialized/real-dump boundaries (open)

Opened 2026-07-31. Not resolved -- this records the investigation so far and
where it honestly stands, including a self-correction partway through.

## The symptom

The About page's "Text Count" trend chart (asambaddhavargīkṛtam included) has
several sharp vertical jumps, concentrated at 2014-06/07, plus more visible in
the 2015 and 2018 ranges. "Effective Size" and "Page Count" are smooth across
the exact same months. The user flagged this as unacceptable and asked
specifically whether recent `build_tree.py` commits (`b579238`,
`a63b21b` -- see [[text-count-inflation]] and CLAUDE.md's "Subpage parenting"
section) introduced a regression, since the chart was smooth two days prior.

## What was ruled out, with direct evidence

**Not a `build_tree.py` regression.** Checked out `550dac9` (the commit
immediately before `b579238`), rebuilt 2014-06 and 2014-07 snapshots from the
*same* cached content-cache inputs via `rebuild_tree_from_cache`, and compared
against current code on those same inputs:

| | June 2014 | July 2014 |
|---|---|---|
| `count` (old code == new code) | 9,053 | 9,402 |
| `text_count`, old code | 4,520 | 4,831 (+311, +6.9%) |
| `text_count`, new code | 1,108 | 2,529 (+1,421, +128%) |

Old and new code agree on classification for every title present in both
months' caches -- zero regressions, confirmed by direct set comparison
(`old_top_noslash ⊇ new_top_noslash`, `old_top_noslash - new_top_noslash`
fully accounted for by real flat-family resolutions, `new - old` empty).
The instability is real, but it is not new code disagreeing with old code on
the same input. It is new code being much more *sensitive* to a difference
in input that was always there but that old code's cruder logic didn't
surface as visibly.

**Not (solely) a "materialized data is systematically worse" story either --
this was checked and is not the whole picture.** `validate_materialization.py`
previously confirmed materialized snapshots track real dumps within ~0.5-0.6%
at the 2022/2025 boundary. If materialized-vs-real were simply "one method is
uniformly less accurate," `count` and `text_count` should both drift by a
comparable, smooth amount. They don't: `count` is smooth (9,053 -> 9,402,
+3.9%, consistent with ordinary monthly growth) while `text_count` is not
(+128% in the new-code run). Since both numbers are computed from the *same*
per-month page set, a real, uniform data-quality gap of the kind already
validated (~1%) cannot by itself produce a 128% swing in one derived figure
while leaving the other untouched. Something is amplifying a real but modest
input difference into a large output difference specifically for `text_count`.

## What was found, concretely

Diffed June's and July's content caches (`content-2014-06-01.json.gz` /
`content-2014-07-01.json.gz`) directly, not just their assembled trees:

- 1,886 Main-namespace titles differ between the two months' `main_pages`
  keys (present in one, absent in the other), despite `count` netting out
  smooth overall.
- ~1,714 titles present in July's cache are **absent from June's cache
  entirely**, yet carry creation timestamps from 2011-2014 -- i.e. they are
  not new content, they pre-date June 2014, and June's snapshot should have
  had them.
- Separately, all 1,028 `ऋग्वेदः सूक्तं M.S` pages (the flat-family-allowlisted
  family, [[text-count-inflation]]) are present in June and **completely
  absent from July** -- also pre-dating June by years (2011 timestamps).
- June 2014 is the last `materialized_months()` entry before real coverage
  resumes at 2014-07 (`pipeline/backfill.py`'s documented hole:
  2012-02 through 2014-06). July 2014 is the first real (legacy-format-archive)
  dump after that hole.

## Where the reasoning went wrong, and where it currently stands

My first write-up concluded this was a materialization-completeness gap and
stopped there, treating "June's cache is missing real pages" as sufficient
explanation on its own. That is incomplete, and the user was right to reject
it: **an incomplete cache alone does not explain why `text_count` swings while
`count` does not.** If June's cache were simply missing ~1,886 pages outright,
`count` would be *lower* in June for that reason alone and the two months
would NOT net out smoothly -- but `count` climbs by an ordinary, smooth 3.9%,
which means whatever's missing on one side is compensated by something
differing on the other side, and the two compensating populations are
evidently very different in *shape* (top-level vs. nested) even though they're
similar in *size*. That shape difference -- not the raw existence of a
cache gap -- is the actual mechanism, and it was never nailed down:

- The 1,028-page ऋग्वेदः सूक्तं disappearance is nested content vanishing
  (no direct effect on top-level count either way).
- The ~1,714 "new to July" titles are a separate population, mostly
  no-slash, mostly top-level by default (no ancestor resolution question at
  all -- they don't contain "/").
- These two populations are not yet shown to be causally related, only
  coincidentally adjacent at the same month boundary. Whether June's cache
  is missing ~1,714 pages that *should* count as texts (making June's
  text_count an undercount) or July's cache has ~1,714 pages that
  *shouldn't* (making July's an overcount) -- or both partially -- is not
  established. Re-materializing June 2014 properly (the user's planned next
  step) would settle this directly rather than by further inference from
  cached artifacts.

## Next step (agreed with user, not yet done)

Re-materialize the affected months (starting with June 2014, likely
extending across the whole `materialized_months()` range near era
boundaries) and re-diff against the current real-dump-backed months.
`dump/_materialize_validation/` already has scaffolding for exactly this kind
of check; CLAUDE.md flags that validation has only been confirmed at the
2022/2025 boundary and explicitly calls out 2014-07 as unconfirmed.

## Standing methodological note

The user's insistence on checking out the actual prior commit and diffing
real rebuilt output (rather than reasoning about the code) was the right call
and should be the default move for "did commit X regress Y" questions in
this codebase, given `pipeline.backfill`'s route-3 cache rebuild makes it
cheap (seconds per month, no network). See CLAUDE.md's "Two on-disk layers
per month" section.
