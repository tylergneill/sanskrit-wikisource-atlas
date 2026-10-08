# Sizes and dates

Part of a pass across all seven repos, coordinated in
`sagara-sangama/notes/scratch/sizes-and-dates.md` -- read that first for what
was decided and why. This file lists only what is left **here**. Delete it
when the boxes are ticked. Written 2026-10-08.

Branch: `fix-sizes-and-dates`.

## Done here

- Sizes are decimal everywhere (1 MB = 1,000,000 bytes).
- `__content_version__` is already the dump's date.

## Left here

- [ ] **Publish `all_stats.sourced`** in `docs/data/tree.json`: the date this
      Atlas took its copy of the collection, YYYY-MM-DD. It must be the same
      value `__content_version__` gets in `docs/VERSION`, written in the same
      place, so the two cannot disagree. `all_stats` is assembled at the end of the tree build in
      `pipeline/process.py`; find where `docs/VERSION` is written and take the
      date from there.
- [ ] Rebuild the tree; commit `tree.json` and `docs/VERSION` together.
