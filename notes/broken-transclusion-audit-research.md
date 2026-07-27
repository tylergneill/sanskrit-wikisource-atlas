# Detecting broken transclusion (missing Commons file) — research in progress

## The problem

Found via manual spot-check: https://sa.wikisource.org/wiki/जातकपद्धतिः renders
completely empty on the live wiki. Its wikitext is just a `{{header}}` template
plus:

```
<pages index="जातकपद्धतिः.pdf" from=1 to=126/>
```

The Index item (`अनुक्रमणिका:जातकपद्धतिः.pdf`) errors with
"दोषः : तादृशी सञ्चिका नास्ति" ("no such file exists") — confirmed the file
`जातकपद्धतिः.pdf` does not exist on Commons (404 at
`commons.wikimedia.org/wiki/File:जातकपद्धतिः.pdf`).

**BUT**: 126 individual `पृष्ठम्:जातकपद्धतिः.pdf/N` leaf pages still exist in
the wiki's database (confirmed in our dump), each with real (if OCR-rough,
quality-level-1) Devanagari text, totaling 476,472 raw bytes. These were
apparently created before the file was deleted/lost from Commons, and never
cleaned up.

Example leaf: https://sa.wikisource.org/wiki/पृष्ठम्:जातकपद्धतिः.pdf/१

## What our pipeline currently does (confirmed correct, not a mirror bug)

`pipeline/process.py`'s `_augment_main_sizes_with_transclusion` correctly
reads the 126 leaves' real text and sums it into the Main page's
`content_bytes` (`docs/data/tree.json`'s `page:जातकपद्धतिः` node shows
476874 raw_bytes / 454734 content_bytes / 243379 transliterated_bytes,
text_count=1, filed correctly under ज्यौतिषम्). This is accurate — the
content genuinely exists in the dump — even though a visitor to the live
wiki can't currently read it there, since ProofreadPage's rendering depends
on the (missing) source file, not the already-stored leaf wikitext.

**So the mirror is arguably MORE useful here** than the live wiki for this
page, since we ingest raw leaf wikitext directly from the dump rather than
depending on live file rendering.

## What the user wants (this is a NEW feature request, not a bug fix)

1. **Detect this pattern systematically** via `pipeline/audit.py` (the
   existing "surfaces likely structural problems for human review" tool —
   see its docstring for the 8 existing checks and their conventions:
   never mutates data, prints a report + optionally updates
   `docs/about.html` between the `AUDIT:START`/`AUDIT:END` markers).
2. For each hit, report the **leaf count** (e.g. "126 hidden pages") and a
   **link to the first leaf page** (e.g. `पृष्ठम्:जातकपद्धतिः.pdf/१`) so a
   human can inspect it directly.
3. **Not currently interested in surfacing this in the mirror's own
   frontend** (docs/app.js) — audit-only, for now.
4. **Bigger idea, contingent on scope**: if this pattern turns out to be
   common enough, consider having `process.py` (not just audit.py)
   artificially represent such a Main page as if it were an untranscluded
   Index item (per CLAUDE.md's existing "Untranscluded Index items"
   convention — an `index-item` node, stats rolled up from leaves, never
   listing individual leaves) rather than a real `page` node — on the
   reasoning that the Main page is "effectively useless without a working
   connection to the transcluded content," so it's honestly more like an
   Index item stuck in the OCR/pre-transclusion state than a real,
   readable Main-namespace text. **Deferred until scope is known.**

## Detection approaches tried/considered so far

### ❌ Ruled out: "Main page's own wikitext is near-empty + has real
transcluded leaf content"

This is the WRONG signal — it matches nearly every healthy transcluded
Main page (a page whose content IS the transcluded scan is the normal,
working case, not a defect). Produces overwhelming false positives.
(`scope_broken_transclusion.py` in scratchpad — abandoned.)

### ❌ Ruled out: "Index item's own `<pagelist />` field is completely
bare (no page-numbering attributes)"

Hypothesis: a healthy Index item usually has ProofreadPage-populated
`<pagelist 1=... 512=- />` style numbering metadata; जातकपद्धतिः's is
bare (`<pagelist />` with nothing inside). Scoped this across the full
dump: **335 Index items** have a totally bare `<pagelist />`, of which
**279** still have leaf pages with real text (the जातकपद्धतिः pattern) —
this looked promising until spot-checking the largest hit,
`वायुपुराणम्.djvu` (1160/1160 real leaves, bare pagelist). Checked the
live page (https://sa.wikisource.org/wiki/वायुपुराणम्) — it renders fine;
it's a healthy top-level wrapper/landing page, no `<pages/>` tag at all,
whose actual content lives in `/पूर्वार्धम्`, `/उत्तरार्धम्` sub-pages.
**Conclusion: bare `<pagelist />` is far too weak/noisy a signal on its
own — most Index items apparently never get manually annotated with page
numbering regardless of whether the underlying file is healthy.**
(`scope_broken_index2.py` in scratchpad — abandoned, but the 279-item
list might still be worth a quick manual skim if pursuing the heuristic
route further, now knowing to filter for pages that actually carry a
`<pages .../>` tag pointing at that Index.)

### ✓ Found the REAL mechanism, but it's not usable from the static dump

MediaWiki/ProofreadPage auto-applies a maintenance category live:
**"Index pages with pagelist tags that refer to a nonexistent file"**
(confirmed present on जातकपद्धतिः's Index page, alongside
"भग्नपरिसन्धियुक्तानि पृष्ठानि" / "pages with broken links" and
"अनुक्रमणिका - Unknown progress"). This is exactly the right concept —
but:
- It's **not stored as a `[[वर्गः:...]]` tag anywhere in the page's own
  wikitext** (confirmed via grep — zero hits for the category name in the
  dump's raw text). It's injected at *render time* by the software doing
  a live Commons file-existence check, so it's invisible to a static XML
  dump entirely. **No way to read this from `pipeline/parse_dump.py`'s
  output, ever, for any dump.**
- Queried the live category page via the API/UI
  (`Category:Index pages with pagelist tags that refer to a nonexistent
  file`) — only **14 members total**, and जातकपद्धतिः **is not among
  them**. So even live, this maintenance category is either scoped more
  narrowly than "file entirely missing" (maybe only a specific malformed-
  syntax variant of the problem), or its membership is stale/lazily
  rebuilt by MediaWiki's job queue and not trustworthy as a complete
  list. Either way: too small and inconsistent to be the answer, even as
  a live lookup.

## Open question when resuming (mid-clarification when session ended)

Was about to ask the user to choose between two remaining paths, given
neither the dump alone nor the "obvious" live maintenance category is
sufficient:

1. **Live Commons check, targeted**: for each Main page whose content
   depends on a `<pages index=X .../>` tag (i.e., `X` resolves to real
   leaf content in the dump), do a live existence check against Commons
   for file `X` (e.g. `commons.wikimedia.org/wiki/Special:FilePath/X` or
   the `imageinfo` API) — one network call per candidate Index, not per
   page. Slower (network-bound, and `pipeline/audit.py` currently runs
   entirely offline against a downloaded dump — this would be a new kind
   of dependency for it), but directly answers the real question instead
   of guessing from wikitext structure.
2. **Pure dump-derived heuristic**, accepting it'll be noisier: e.g. flag
   an Index item where (a) it has leaf pages with real text, AND (b) the
   highest actual leaf number is far short of what the Main page(s)'
   `from=`/`to=` ranges expect, AND/OR (c) explore whether `Progress=`
   field values (`OCR` vs `Proofread` vs `Validated`) combined with some
   other structural check correlates better than bare-`<pagelist/>` did.
   Not yet explored in depth — worth another pass at candidate signals
   before committing to the network-call approach, since audit.py's
   "never touches the network, works offline against one dump file"
   property is a real design property worth preserving if possible.

Need the user's input on which direction to pursue (or a third option)
before writing any detection code.

## Scope-then-decide reminder

Once *some* working detection exists (whichever approach), the very next
step is exactly what was being scoped: run it across the full corpus,
see how many Main pages are affected, and only THEN decide whether the
"represent as artificial untranscluded Index item" idea (point 4 above)
is worth implementing in `process.py`, or whether audit-only reporting
is enough.

## Related, unrelated-but-adjacent open item from this session

Separately (not part of this investigation, don't conflate): the user
also asked for a plan on richer backfill snapshots (caching
`ContentIndex`-equivalent per-page data alongside each historical
month's `dump/_backfill_snapshots/tree-<date>.json`) so future
`build_tree_json`-only logic bugs — like the redirect-stub fix landed
this session (commit `5e0c6db`) — don't require a full 8-10 hour
backfill re-run just to re-run the cheap tree-assembly step. That plan
was requested but NOT YET WRITTEN — still owed as a separate .md when
picked back up.
