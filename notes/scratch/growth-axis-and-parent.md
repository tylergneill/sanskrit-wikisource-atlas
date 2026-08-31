# This Atlas is the reference implementation for axis 2

Written 2026-08-19 from the cross-atlas design session. Full design in
`sagara-sangama/notes/changelog-axes.md`. Little is required of this Atlas —
this note exists so the reasoning is not re-derived from scratch here.

## Two axes

**Axis 1 — per-item last updated.** Provided.
**Axis 2 — collection growth.** Provided, and **this is the only Atlas where it
is genuinely measured.**

The reason is the historical snapshots: monthly states materialized from the
meta-history dump back to 2012-02 and diffed. Arrivals *and* removals are
observed. The siblings each hold a single snapshot 1–1.5 years old, so neither
can see a change directly:

- **sanskrit-documents** approximates growth from each document's own
  `Latest update`. Measured: 97.7% of documents go untouched over 18 months, so
  last-updated closely tracks arrival — with the earliest years a lower bound.
- **e-bharatisampat** infers it from serial order, corrected against img
  filename timestamps.

Two structural consequences worth knowing when reading sibling code:

- `items_removed` and `items_changed_count` are **always zero** in the
  siblings. A single observation cannot see a removal. Those fields are
  meaningful *here* and nowhere else.
- This Atlas's delta machinery has no counterpart in the siblings, and is
  explicitly out of scope for the unified parent graphic.

## The shape, which this Atlas already sets

    docs/data/tree.json        structure + all_stats   -> the MAIN page (app.js)
    docs/data/changelog.json   the time series          -> the ABOUT page (about.js)

Both siblings already follow it. Axis 1 stays on each Atlas's own page; **axis
2 is what the parent consumes.**

## Open

- [ ] Decide the transport to the parent: widen `CONTRACT.md` to read
      `changelog.json` as a second artifact, or publish a compact
      `{period, cumulative}` series into `all_stats`. Leaning toward the
      latter — the three changelogs do not share a schema (this one and SD's
      are lists, EBS's is a dict), and the parent does not need per-item
      detail. Cross-atlas decision.
- [ ] Whichever is chosen, publish **what kind** of series this is
      (`measured`, against the siblings' `approximated` / `inferred`) so the
      parent can caveat per-series rather than blanket-disclaiming all three.
- [ ] This Atlas is the only one publishing `last_changed` in `all_stats`; the
      siblings should follow. See `sagara-sangama/notes/all-stats-uniformity.md`.
