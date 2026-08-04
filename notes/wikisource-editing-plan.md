# Wikisource editing plan

Plan for directly editing sa.wikisource.org content (not just the atlas
pipeline) to fix structural gaps the atlas surfaces but can't paper over
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

## Breadcrumb failures: four distinct modes, two different fixes

Investigated 2026-07-30, prompted by असम्बद्धवर्गीकृतम् showing 5,359 texts
against the central tree's 2,217. Browsing it showed rows like
`अवन्तीखण्डः/अवन्तीक्षेत्रमाहात्म्यम्/अध्यायः ८` sitting at top level
*despite* correct `/` syntax — so "fails to roll up" was covering several
unrelated causes at once, only some of them the wiki's fault.

**Two were pipeline bugs, now fixed** (`build_tree._resolve_ancestor`), and
no longer need any on-wiki action:

1. **Missing intermediate ancestor.** `ऋग्वेदः/संहिता/सस्वरपाठः/१-५` where
   `ऋग्वेदः/संहिता/सस्वरपाठः` was never created. The old code gave up at the
   first miss instead of walking further up. ~270 pages.
2. **Whitespace around the slash.** `अब्धिनौयानमीमांसा /चतुर्थं खण्डम्`.
   MediaWiki normalizes this when resolving subpages, so the breadcrumb was
   always genuinely correct on-wiki; only the atlas was failing. ~110 pages.

**Two are real wiki problems**, surfaced by the audit section on
`docs/about.html` (see there for live counts, not this note):

3. **Non-slash separators.** `महाभारतम्-03-आरण्यकपर्व-001` and friends — a
   hyphen or dot where `/` belongs. A hyphen carries no structural meaning
   on MediaWiki, so nesting these would be a naming-convention guess; the
   atlas deliberately leaves them flat. महाभारतम् alone accounts for ~2,314
   pages, so a single work's cleanup would reclaim most of this category.
4. **Unresolvable paths.** Pages using `/` whose breadcrumb names a page that
   doesn't exist at any level. ~107 pages under ~20 missing roots.

### The fix is a page MOVE, not a redirect

Easy to get backwards, so worth stating plainly. `build_main_tree` resolves a
*parent title* through redirects. That means a redirect only helps when the
**content page's own title already contains `/`** — the रामायणम् →
वाल्मीकिरामायणम् case, where the parent segment is a redirect and the child
re-parents onto the real target.

Creating a redirect *at* a new slash title, pointing at a flat page, does
**nothing**: the content still lives at a slash-free title, and redirect
stubs are skipped as invisible pointers (`5e0c6db`) rather than re-parented.

So the effective operation for modes 3 and 4 is **moving the page** to proper
`Work/Part` syntax — which leaves a redirect behind at the old title
automatically, so nothing breaks for anyone following an existing link. It's
non-destructive: no existing page is edited, nothing is deleted, and old
titles keep resolving.

### A redirect title used as a de-facto index for a live subtree

Worth calling out separately, because it's easy to describe too narrowly. A
redirect isn't only an alternate name that happens to sit in a breadcrumb —
it can have **a whole subtree of real content titled under it**.

`श्रीमद्भागवतपुराणम्` is the real work page (1,129 bytes, not a redirect).
`श्रीमद्भागवत महापुराण` is a 36-byte redirect stub pointing at it — and 183
real content pages are titled `श्रीमद्भागवत महापुराण/…`, i.e. the redirect's
name is doing duty as the index for its own branch of chapters, alongside the
423 titled under the real name.

`_resolve_ancestor` now merges these: resolution happens at every path level,
so `श्रीमद्भागवत महापुराण/माहात्म्य (पाद्मे)/अध्यायः ०१` re-parents onto
`श्रीमद्भागवतपुराणम्/माहात्म्य (पाद्मे)` — an intermediate level in the *other*
naming convention entirely. Both families collapse into one tree, so this
fixes not just a redirect sitting in a parent path but two sibling subtrees
split across two title conventions.

### Partially overlapping title conventions (not simply duplicate editions)

Related, and worth checking before consolidating anything by hand. Where the
same work exists under two conventions, the two sets **overlap partially**
rather than duplicating cleanly: 183 pages under `श्रीमद्भागवत महापुराण/…`
against 423 under `श्रीमद्भागवतपुराणम्/…`, so some chapters exist in both
namings and others in only one. A chapter pair sampled across the two measured
0.92 Jaccard token overlap (sharing the opening verse verbatim), which
establishes that *those two pages* are the same text — it does **not**
establish that either family is redundant as a whole.

By contrast the two कौटिलीय अर्थशास्त्र families are ~1.1MB vs ~104KB, a
near-complete text and a barely-started one — not duplicates in any sense.

So "these two look like the same work" is the start of an investigation, not a
finding. Which pages are genuinely redundant, and which naming should win, is
an editorial judgment (recension, edition, contributor intent) the audit
deliberately doesn't make.

## Broken Commons transclusions (missing scan file)

A separate, unrelated structural gap, now also surfaced live by
`docs/about.html`'s audit section ("Transclusions broken due to removal of image file from Commons") — see that page for the current count/list.
Found via manual spot-check (जातकपद्धतिः): the Index item's backing scan
file (e.g. `File:जातकपद्धतिः.pdf`) has been deleted/lost from Commons, so
the live wiki page renders completely empty (ProofreadPage's rendering
depends on the file, not the already-stored leaf wikitext) even though the
atlas still shows real content, since it reads leaf wikitext directly from
the dump rather than depending on live file rendering. See
`pipeline/audit.py`'s `find_broken_commons_transclusions` docstring for the
detection mechanism (a live, batched Commons `action=query` check — not
detectable from the dump alone).

This is purely an on-wiki fix, not something the atlas pipeline can paper
over:

1. **Preferred**: someone re-uploads the missing scan file to Commons under
   the same filename (a copy of the original PDF/DjVu may exist on
   archive.org, in a personal backup, or via re-scanning the physical
   source) — restores real transclusion immediately, with no wikitext
   changes needed anywhere.
2. **Fallback**, if the original file truly can't be recovered: manually
   copy the stored leaf-page text into the Main page's own wikitext,
   replacing the `<pages .../>` tag with the actual prose. Uglier and loses
   page-by-page scan alignment (no more jumping to a specific scanned leaf
   image), but preserves the readable text for visitors to the live wiki.

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
