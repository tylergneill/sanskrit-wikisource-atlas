# Wikisource editing plan

Plan for directly editing sa.wikisource.org content (not just the mirror
pipeline) to fix structural gaps the mirror surfaces but can't paper over
on its own. Started 2026-07-22 from a UI bug report: Tantrāloka's chapter
pages showed up disconnected from their TOC page, in the orphan bucket
(असम्बद्धवर्गीकृतम्).

The structural findings that originally motivated this plan (missing `/`
breadcrumb syntax; parent pages lacking a Category shared by all
subpages) are now surfaced live by `docs/about.html`'s audit section
("Data Quality: Structural Errors and Provenance Hints") — see that page
for current counts/lists rather than this note, which would go stale. A
third finding, silent subpage category divergence, was a pipeline bug,
already fixed (`35489f1`) — subpages with their own diverging category tag
now get their own independently reachable page node, same treatment
top-level multi-tagged pages get.

## Practical tooling / starting points for hand-editing

- **Precedent to cite in edit summaries**: मनुस्मृतिः (Manusmṛti) is a
  well-proofread, actively-maintained text using correct `/` syntax
  throughout (`मनुस्मृतिः/प्रथमोध्यायः`, etc.) — a stronger, wiki-local
  justification than an abstract "MediaWiki best practice" claim.
- **Tool**: HotCat for manual category-tag fixes (orphan-bucket triage);
  page moves for adding `/` syntax to breadcrumb-gap pages. Both are
  ordinary manual edits with no special rate ceiling.
- **Suggested pilot batch**: Tantrāloka (~27-37 pages depending on count
  method) — page moves to `/` syntax + category tags. Stay under ~100
  edits for a first sitting to keep the batch reviewable.
- **Largest single breadcrumb-gap case**: ऋग्वेदः (Ṛgveda), 1,041 pages —
  large enough to warrant its own dedicated pass given likely internal
  naming quirks (sūkta/maṇḍala numbering); see current counts on the About
  page audit section before starting.
- **Automation**: worth exploring scripted/bot tooling (e.g. Pywikibot)
  with a proper bot flag, to scale past hand-editing for large cases like
  Ṛgveda. Gate this on manual edits first, though — only pursue automation
  once a batch of hand edits has gone through and stuck without being
  reverted. Bot-flagging and scripted edits before establishing that the
  edits themselves are wanted risks a much larger cleanup if they turn out
  to be contested.
