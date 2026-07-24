# ProofreadPage transclusion undercount fix

Open pipeline bug, surfaced 2026-07-24 during a systematic fact-check of
`docs/about.html`'s claims against `pipeline/*.py` and live generated data
(the fact-check itself is done and its other findings already fixed in
About page copy -- this note keeps only the one item still requiring an
actual code fix).

## The bug

**Claim** (About page, "Calculating Size"): "this mirror parses the
wikitext, strips out overhead, populates transcluded content, and
transliterates from Devanāgarī to IAST" -- i.e. `content_bytes` is supposed
to be a meaningful "how much real text is here" figure that already accounts
for transclusion.

**Reality**: `pipeline/content_size.py`'s `compute_content_size` only calls
`expand_templates` (template transclusion, `{{...}}`) -- it never expands a
`<pages index="..." />` ProofreadPage transclusion tag into the real
पृष्ठम्:Title/N leaf-page text that tag actually renders as. For a Main page
built entirely by transcluding proofread scan pages (extremely common --
this is the standard way large OCR'd texts get published to Mainspace), the
wikitext literally IS just that `<pages ... />` tag (or close to it), so
`content_bytes` measures almost nothing.

**Verified against live data** (`docs/data/tree.json`, 2026-07 dump): of
1,309 Main pages that transclude at least one Index item's leaves (i.e.
have a non-empty `source_indexes`, see `pipeline/transclusion.py`'s
`build_reverse_transclusion_map`), **576 (44%) show `content_bytes` under 50
bytes** -- e.g. **काठकोपनिषत्** (a real, complete Upanishad text) shows
`content_bytes: 0` despite being a substantial published work. Its
`transliterated_bytes` and displayed size are similarly wrong (derived from
`content_bytes`).

**Why this matters**: every size/count rollup the mirror shows (per-page,
per-category, root total) is silently missing a large fraction of the
corpus's real byte count wherever ProofreadPage transclusion was used
instead of hand-typed wikitext -- disproportionately the case for large,
systematically-scanned works. This likely also affects `text_count`/
size-based sorting, and definitely affects the "effective size" headline
figures and the changelog's size trend charts.

## Checked hypothesis: are these just unpopulated proofreading stubs?

Not stubs -- confirmed via direct dump inspection. काठकोपनिषत् transcludes
पृष्ठम्:काठकोपनिषत्.djvu/१ through /२०५ (205 leaves); scanning those leaves
directly shows 201 real leaf records totaling **1,072,365 raw bytes** of
actual Sanskrit text (title page, table of contents, running commentary),
not empty scaffolding. Three more sampled titles (आर्यभटीयम्, वेदान्तसारः,
आयुर्वेदप्रकाशः) showed the same pattern: 664KB-1.1MB of real leaf content
each, sitting behind a Main page whose own `content_bytes` reads near-zero.

Full site-wide survey of all 127,746 पृष्ठम् (namespace 104) leaf records'
`<pagequality level="N">` tags and raw byte totals:

| quality level | meaning | leaf count | raw bytes |
|---|---|---|---|
| 0 | Without text | 171 | 61,222 |
| 1 | Not proofread | 80,473 | 337,631,814 |
| 2 | Problematic | 289 | 1,034,684 |
| 3 | Proofread | 31,012 | 132,606,658 |
| 4 | Validated | 13,792 | 50,677,804 |
| (no tag found) | -- | 2,009 | 0 |

Only level 0 ("Without text") is genuinely empty (61KB site-wide,
negligible). Level 1 ("Not proofread," 63% of all leaves) means *raw OCR
text exists but hasn't been human-verified yet*, not "empty" -- 337.6MB of
real, readable text, more than every other quality tier combined.

## Decision (confirmed with user, 2026-07-24): count all quality levels

Quality level 1 ("Not proofread") text is still real, readable content --
count it the same as levels 2-4, same as `compute_page_ns_rollup` already
does for untranscluded Index items (no quality distinction there either).
Only quality-0 (genuinely empty) contributes nothing, which falls out
naturally from summing real byte lengths -- no special-casing needed.

## Is this bug responsible for the "0.0" sizes visible in the frontend?

Mostly, but not entirely. Surveyed every page-node occurrence in
`docs/data/tree.json` with `content_bytes == 0`: **813 total, of which 571
(70%) have a non-empty `source_indexes`** (i.e. are this exact bug) -- the
remaining 242 (30%, 173 distinct titles) have no ProofreadPage transclusion
involved at all.

Classified all 173 distinct titles directly against the raw dump wikitext:

- **151/173 are genuine table-of-contents/navigation pages** -- an
  `<inputbox>` full-text-search widget (5), or a `{{header}}` template
  (expands to near-nothing on its own) plus nothing else but a bare list of
  `[[wiki links]]` to the actual chapters (146). Their `content_bytes: 0`
  is legitimately correct -- there really is no prose on that specific
  node. Overlaps with (but is a distinct symptom from)
  `pipeline/audit.py`'s existing "breadcrumb-gap candidates" check -- not a
  second bug to fix as part of this note.
- **2/173 are a separate, third bug** (see below).
- The remaining ~20 are almost certainly also TOC pages (confirmed by
  direct inspection) that a regex classifier just didn't auto-recognize
  cleanly -- not worth chasing further by hand.

**Takeaway for validating the fix**: once implemented, expect roughly
151/173 (≈87%) of the non-transclusion "0.0" pages to still legitimately
show near-zero -- don't expect the fix to zero out every "0.0" in the UI.

## A separate, third bug found along the way (not yet fixed)

`_strip_navigation_lines` (in `pipeline/content_size.py`) false-positives
on short, punctuation-free verse/mantra text, stripping it to zero. That
function drops any run of 5+ consecutive short (<40 char) lines lacking
`।॥.!?` punctuation, on the theory that this pattern is a chapter-link
list, not real prose. But **शनि मंत्र** and **दत्तलीला मंत्र** are both
real, short devotional mantras -- each pāda is short and ends in a plain
colon (`:`) rather than a daṇḍa (`।`/`॥`), so every line matches the
"navish" heuristic and the entire verse gets deleted. Confirmed directly:
शनि मंत्र's raw wikitext contains a full 12-line mantra inside `<poem>`
tags (1037 raw bytes); दत्तलीला मंत्र is a single one-line mantra phrase.

**Not yet investigated further or fixed** -- likely low-prevalence
(colon-terminated, punctuation-free short verse appears uncommon in the
corpus, only 2 found within a 173-title sample) but real. If pursued, the
fix would be narrowing `_strip_navigation_lines`'s heuristic (e.g. also
treating repeated near-identical bracket/pipe syntax remnants, not just
"short + no daṇḍa", as the real navigation signal) rather than loosening
the length/punctuation thresholds generally, which risks reintroducing real
navigation lists as false negatives. Separate task from the main
transclusion fix below -- don't conflate the two fixes into one PR.

## A related false claim on the About page (fix after the code fix lands)

The "Transclusion" section (`docs/about.html`) currently says: "Where any
range *has* been transcluded, the mirror simply shows the Mainspace page as
the real content..." -- not true today, since the Mainspace page's
`content_bytes` (and therefore its displayed size) is near-zero garbage for
the 44% of transcluding pages measured. Correct this sentence once the
pipeline fix lands (exact wording depends on what the fix actually
produces) -- don't patch it now, since patching it twice would be wasted
effort.

## Implementation notes for the actual fix (not yet written)

Decision on quality-level handling is resolved (count all levels 0-4, per
above). Remaining work:

1. Detect `<pages index="..." from=X to=Y />` per Main page (see
   `pipeline/transclusion.py`'s `_PAGES_TAG_RE`/`transcluded_index_titles`).
2. Resolve `from`/`to` against the correct पृष्ठम्:Title/N leaf records.
   **Gotcha**: the tag's `from`/`to` are Arabic numerals, but leaf titles
   use Devanagari numerals -- confirmed via काठकोपनिषत्'s leaves, titled
   `.../१`, `.../३`, `.../५`... not `.../1`, `.../2`, `.../3`. A `from=1
   to=205` range must map onto that Devanagari-numbered sequence correctly
   (by leaf *position*, not by string-matching the Arabic numerals
   literally against Devanagari-numbered titles).
3. Sum/substitute the resolved leaves' real content the same way
   `expand_templates` substitutes template bodies -- likely
   reusing/extending `compute_page_ns_rollup`'s existing per-leaf size
   computation rather than re-deriving it.
4. **Gotcha**: multiple sibling Main pages can transclude *different
   sub-ranges of the same Index* -- confirmed via सामवेदः/कौथुमीया/
   आर्षेयब्राह्मणम्/अध्यायः १ through ५, all sourced from the single Index
   item आर्षेयब्राह्मणम्.djvu. The fix must slice each Main page's own
   `from`/`to` range out of that Index's leaves, not attribute the Index's
   full content to every Main page that references it.
5. Once implemented: rerun `pipeline/audit.py`'s IAST-ratio-adjacent
   check (or just re-derive root stats) since `transliterated_bytes /
   content_bytes` will shift again after this fix -- don't treat the
   current ~51% figure in `docs/about.html` as final.
6. Correct the About-page "Transclusion" section claim (see above) once
   the fix is live and its actual before/after behavior is known.
