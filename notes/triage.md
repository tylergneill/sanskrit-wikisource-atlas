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
Two open-task notes remain.

**Conclusion: neither blocks launch.** Both are backlog items, not
correctness blockers.

- **Fourth source era** (`fourth-source-era.md`) — purely about extending
  the historical changelog backward another 2.5 years (2012-2014). The
  changelog already works correctly for the range it covers; this is
  "more history would be nice," not a bug. Zero effect on the live corpus
  browser at all.

- **Orphan-bucket vs. `find_orphaned_categories` divergence**
  (`orphan-bucket-vs-orphaned-categories.md`) — this is a note about two
  *audit-tooling* diagnostics disagreeing with each other for structural
  reasons, not about either one being wrong for its own purpose. The
  orphan bucket itself (what users actually browse) still correctly
  surfaces every unreachable page/category — it's just that the top-level
  shape within the bucket doesn't perfectly mirror category parent/child
  structure. Cosmetic/audit-precision issue, invisible to a typical
  reader.

Neither affects correctness of what's shown for the well-categorized
ग्रन्थाः corpus (the "central" tree), which is the part carrying the real
launch stakes. Ship as-is; treat both as normal backlog items.
