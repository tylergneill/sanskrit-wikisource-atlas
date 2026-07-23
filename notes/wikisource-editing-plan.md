# Wikisource editing plan

Plan for directly editing sa.wikisource.org content (not just the mirror
pipeline) to fix structural gaps the mirror surfaces but can't paper over
on its own. Started 2026-07-22 from a UI bug report: Tantrāloka's chapter
pages showed up disconnected from their TOC page, in the orphan bucket
(असम्बद्धवर्गीकृतम्).

## Background: three distinct problems, one root cause investigation

`pipeline/build_tree.py`'s `build_main_tree()` derives Main-namespace
parent/child purely by splitting page titles on `/` — no heuristics, by
design (see its docstring). Category filing (`pipeline/process.py`,
`build_category_membership_maps`) is a *separate*, unrelated mechanism
driven purely by each page's own direct `[[वर्गः:...]]` tags.

Investigating Tantrāloka surfaced that these two mechanisms interact badly
in three distinct ways, all upstream data problems on the wiki itself
(confirmed against a live dump export dated 2026-07-01), not pipeline bugs
in the strict sense:

### 1. Missing `/` subpage syntax ("breadcrumb" gaps)

Many multi-chapter works were never given real `/`-delimited subpage
titles by their editors. Tantrāloka's chapters are titled
`तन्त्रालोकः अष्टममाह्निकम्` (space) instead of
`तन्त्रालोकः/अष्टममाह्निकम्` (slash) — so they were never real MediaWiki
subpages, on the wiki OR in the mirror. This is a genuine gap in how the
source content is organized, not something the mirror can infer around
safely.

**Confirmed correct-convention exemplar**: मनुस्मृतिः (Manusmṛti), a
well-proofread, actively-maintained text on the same wiki, uses proper `/`
syntax throughout (`मनुस्मृतिः/प्रथमोध्यायः`, etc.). Cite this precedent
in edit summaries rather than an abstract "MediaWiki best practice" — it's
a stronger, wiki-local justification.

**Scope** (live dump, 2026-07-01):
- **221 works** affected, **1,799** chapter/part pages implicated total
- **1,727** of those (96%) currently sit in the orphan bucket
- **173 works** have ALL their chapters affected this way
- Largest single case: **ऋग्वेदः** (Ṛgveda), 1,041 pages (individual
  sūkta + maṇḍala pages) — more than half the total volume by itself
- Next tier: समराङ्गणसूत्रधार (83), पञ्चतन्त्रम् (51), then तन्त्रालोकः /
  हितोपदेशः / अष्टाङ्गसंग्रहः (~27 each)
- **419** = number of top-level pages that currently have at least one
  *real* `/`-subpage already — i.e. works that already use the correct
  convention somewhere. This is a more meaningful "structural complexity"
  metric than a raw page count (worth surfacing in the sidebar/About page
  as a corpus-health stat, separate from this cleanup campaign).

### 2. Orphan-bucket triage (uncategorized/miscategorized content)

The असम्बद्धवर्गीकृतम् bucket holds every page/Index item unreachable from
root by category descent — no tag, or a tag that itself never resolves to
root. Requires manual, one-by-one judgment calls to assign real
categories; not mechanically fixable. Fixing (1) above will also reduce
this bucket somewhat, since properly-linked chapters that inherit no
category from their parent will still need their own explicit tag or will
need the parent itself fixed (see workstream 2 below).

### 3. Silent subpage category divergence (the big one)

Even where `/` subpage syntax IS used correctly, `build_page_node()` in
`pipeline/process.py` rolls up every real breadcrumb-subpage into its
top-level ancestor's category filing **unconditionally** — a subpage's own
direct category tags, if any, are completely ignored for filing purposes.
The subpage's stats fold into wherever its ancestor is filed; its own tag
is silently dropped from the output entirely (no pointer, no second
filing, nothing).

**Measured scope** (live dump, 2026-07-01): of 22,625 breadcrumb-subpages
under the 419 top-level pages that have real subpages —
- **3,100** carry at least one direct category tag of their own
- **1,823** of those differ from the parent's tags
- **1,700** carry a category not present on the parent at all

This is not noise — the divergences are frequently meaningful:
- `महाभारतम्/शांतिपर्व` tagged with its own parvan-specific category,
  distinct from parent's generic `महाभारतम्`
- `कामसूत्रम्` chapters tagged `कामसूत्रम्` itself, while the parent page is
  tagged the broader genre `कामशास्त्रम्`
- `मेघदूतम्` cantos tagged general `काव्यम्` (poetry), while the parent is
  tagged the specific genre `सन्देशकाव्यानि` (messenger-poems)

**Decision**: this is the highest-priority of the three problems. The
mirror needs to stop silently dropping subpage-level category tags and
instead surface multiple categories per page/subpage, the same way it
already handles multi-parented categories and multi-tagged top-level pages
(`page-pointer` / `category-pointer` nodes). This is a pipeline/frontend
fix, not a wiki-editing task — no wiki edits are needed to expose data
that's already present in the dump.

## A related, adjacent finding: dead category-inference code

`pipeline/transclusion.py`'s `infer_root_categories()` implements "if
every subpage under a root shares a category the root itself lacks, infer
that category onto the root" — matching the About page's claim: *"if every
subpage is found to share a Category that the parent itself lacks, then
the Category is indeed inferred onto the parent and visibly marked as
such."*

**This function is never called.** Confirmed via full-repo grep and
`git log -S infer_root_categories` — it has been dead code since the
commit that introduced it. No frontend badge/marker exists for it either.
The About page's claim is currently false as shipped.

Only **2 cases** in the current corpus would qualify for inference as
speced: रामायणम् (children agree on वाल्मीकिरामायणम्) and कृष्णयजुर्वेदः.

**Decision**: do NOT wire up silent inference. Silently inferring the
children's shared category onto the parent would conceal a real upstream
tagging gap rather than surface it for a human to fix. Instead, build this
detection into tooling that flags these cases for manual correction
upstream on the wiki (add the real category tag to the parent page
directly) — consistent with the "no heuristics/estimates" philosophy
already governing the rest of the pipeline. The About page's text should
be corrected to describe a detector, not an automatic inference, once this
is implemented.

## Wiki community / editing-norms findings (as of 2026-07-22)

Tyler has zero edit history on sa.wikisource.org as of this plan.

- No local bot policy; sa.wikisource's "Bots" page is a stub pointing to
  English Wikisource's policy (adopted by reference).
- Zero bureaucrats currently listed — no one locally empowered to grant a
  bot flag without likely Steward escalation.
- Two sysops: **Shubha** (also the most active content editor recently) and
  one other, less active account. Shubha is the realistic point of
  contact if friction arises.
- The `>1 edit/minute` unflagged-bot rate limit applies only to
  automated/scripted tools (e.g. Pywikibot), not to fast manual editing —
  HotCat used by hand, however quickly, is still a manual edit with no
  rate ceiling.
- Community is small but real: active proofreaders (Shubha, Geeta g
  hegde, Swaminathan sitapathi) plus at least one editor doing routine
  manual HotCat category fixes — meaning ad hoc category-tag edits are
  already normal, unremarkable activity on this wiki.
- **Decision**: skip a Scriptorium (village pump) introduction post for
  now. Start with a small, manual batch (<100 edits, HotCat / hand page
  moves), not scripted/bot tooling, and reassess after building real edit
  history.

## Recommended sequencing

1. **Workstream 3 (pipeline/frontend fix)** can start immediately,
   independent of any wiki edits — the data already exists in the dump,
   it's a mirror-side surfacing gap. Also implement the inference
   *detector* (not silent inference) from the dead-code finding above
   while touching this area.
2. **Workstream 1 pilot**: Tantrāloka (~27-37 pages depending on count
   method) as the first small, hand-edited batch — page moves to `/`
   syntax + category tags, citing the Manusmṛति precedent in edit
   summaries. Stay under ~100 edits for the first sitting.
3. **Workstream 2** (orphan-bucket triage) can proceed in parallel in
   small batches via HotCat, wherever a confident category judgment call
   is possible — no dependency on workstream 1.
4. **Scale up** workstream 1 to the remaining ~220 works only after the
   pilot batch lands cleanly with no pushback; revisit whether
   scripted/bot tooling and a bot-flag request are worth it once there's
   real edit history to point to. Ṛgveda (1,041 pages) is large enough to
   warrant its own dedicated pass, given its size and likely internal
   naming quirks (sūkta/maṇḍala numbering).
