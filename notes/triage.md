# Launch triage: do any open notes block first public release?

Assessed 2026-07-24, weighing the three remaining open-task notes
(`fourth-source-era.md`, `orphan-bucket-vs-orphaned-categories.md`,
`silent-subpage-category-divergence.md`) against releasing the upgraded
tool to the public for the first time.

**Conclusion: none block launch.** All three are backlog items, not
correctness blockers.

- **Silent subpage category divergence** — this is data *loss* (dropped
  tags), not data *corruption*: nothing is displayed incorrectly, some
  subpages just aren't filed under a second category they arguably
  deserve. The frontend behavior for these pages is unaffected —
  Tantrāloka's chapters still render correctly under their real parent,
  they just don't additionally show up under their own more specific
  category. That's a completeness gap, not a wrong answer. Worth fixing
  soon, but it's the kind of thing a "we're actively improving
  categorization" changelog entry down the line can address.

- **Fourth source era** — purely about extending the historical changelog
  backward another 2.5 years (2012-2014). The changelog already works
  correctly for the range it covers; this is "more history would be
  nice," not a bug. Zero effect on the live corpus browser at all.

- **Orphan-bucket vs. `find_orphaned_categories` divergence** — this is a
  note about two *audit-tooling* diagnostics disagreeing with each other
  for structural reasons, not about either one being wrong for its own
  purpose. The orphan bucket itself (what users actually browse) still
  correctly surfaces every unreachable page/category — it's just that the
  top-level shape within the bucket doesn't perfectly mirror category
  parent/child structure. Cosmetic/audit-precision issue, invisible to a
  typical reader.

None of these affect correctness of what's shown for the well-categorized
ग्रन्थाः corpus (the "central" tree), which is the part carrying the real
launch stakes. Ship as-is; treat all three as normal backlog items.
