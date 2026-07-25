# Launch triage: do any open notes block first public release?

Assessed 2026-07-24, weighing the open-task notes against releasing the
upgraded tool to the public for the first time.

**Correction (same day, later pass):** the original version of this note
weighed three open items, including "silent subpage category divergence."
That item turned out to already be fixed, in commit `35489f1`
(2026-07-22) — predating this triage entirely. Subpages carrying their own
diverging category tag now get their own independently reachable page
node, same treatment top-level multi-tagged pages already get. Its note
(`silent-subpage-category-divergence.md`) has been deleted as resolved.

**Update (2026-07-25): fourth source era implemented and shipped.**
`fourth-source-era.md`'s proposal — extending the historical changelog
backward another 2.5 years (2012-02 through 2014-06) via a second
materialization gap — was implemented, validated against the 2014-07
boundary (~2-3% deviation, explained by that boundary dump's real date
being 2014-07-14 not 2014-07-01), and confirmed live: a full from-scratch
`make backfill` run added exactly the 29 expected new monthly transitions
with zero losses, taking `docs/data/changelog.json` from 123 to 152
entries and its earliest coverage from 2014-07 back to 2012-02. See
`CLAUDE.md`'s "Historical backfill and the changelog" section for the
maintained documentation; this note itself has been deleted as resolved,
same treatment as the subpage-divergence note above.

One open-task note remains.

- **Orphan-bucket vs. `find_orphaned_categories` divergence**
  (`orphan-bucket-vs-orphaned-categories.md`) — this is a note about two
  *audit-tooling* diagnostics disagreeing with each other for structural
  reasons, not about either one being wrong for its own purpose. The
  orphan bucket itself (what users actually browse) still correctly
  surfaces every unreachable page/category — it's just that the top-level
  shape within the bucket doesn't perfectly mirror category parent/child
  structure. Cosmetic/audit-precision issue, invisible to a typical
  reader. Does not block launch.

Doesn't affect correctness of what's shown for the well-categorized
ग्रन्थाः corpus (the "central" tree), which is the part carrying the real
launch stakes. Ship as-is; treat it as a normal backlog item.
