# The last of the fulltext coverage: two fixes

**Status: specified, not built.** Fix 1 is a decision; fix 2 is mechanical once
fix 1 is settled.

## Where coverage actually stands

Five ways a work is assembled. Four are handled (`pipeline/process.py`,
rivulet's `extract/wikisource/text_extractor.py`):

| form | how the parts are found |
| --- | --- |
| subpage children | `main_nodes`, the redirect-resolved subpage tree |
| redirect-renamed children | same — `वाल्मीकिरामायणम्` holds `रामायणम्/…` |
| transcluded scan leaves | `resolve_transcluded_leaves` |
| untranscluded Index items | `untranscluded_leaves_by_index` |

**Of items visible in Sāgarasaṅgama's search, 3655/3729 (98%) carry a `txt`
link.** The other two collections are at 100%.

Unpopulated scans no longer count against this: an Index item with no content
anywhere is flagged `po` and hidden by default, the same treatment
e-bhāratīsampat gives its scan-only works (commit `5e338c6`). That moved the
published `text_count` 3805 → 3729.

**These are called "image only" here, not "PDF only"** — the scans are `.pdf`,
`.djvu` and `.tif` alike, while e-bhāratīsampat's really are all PDFs. The flag
in `search.json` is `po` for both; only the label differs.

## What is left: 74 items

Every remaining visible item without a fulltext link:

```
 26   link-list pages that WOULD qualify for rollup      <- fix 1
 24   header/stub pages, no wikilinks at all             nothing to point at
 21   link-lists where at least one link is a redlink    partial work
  6   link-lists with only one link                      not a work
  3   link-lists whose targets have no text              nothing to point at
  ——
 80   (74 visible; 6 overlap with items counted elsewhere)
```

Plus **8 items with content under 5 KB** (down to 84 bytes) that are too small
to be real works — `मुखपुटम्` (the main page), `गणितसारसङ्ग्रहः` at 102 bytes.

So the realistic ceiling is **~99%**, not 100%. Most of what remains has no
text anywhere in the corpus — a limit of Wikisource, not of this pipeline.

---

# Fix 1 — link-list rollup (26 items, 98% → ~99%)

## What it is

A page that is a header plus a **bulleted list of `[[wikilinks]]`** to
separately-titled works. `तन्त्रालोकः` is 1420 bytes of wikitext linking 37
āhnikas, each its own Main page with its own text. It strips to zero content,
so it has no fulltext — while the Atlas still lists it.

## Why it needs a test, unlike the other four

The handled forms rest on a structural claim of ownership:

- a subpage title (`Work/Chapter`) says "I am part of Work"
- `<pages index="X.djvu" from=1 to=205/>` says "my content is X's leaves 1–205"

**A wikilink says nothing.** `[[X]]` means "see X" as readily as "X is my
chapter 3". Rolling up every link-list would concatenate genre anthologies into
works that do not exist.

## The proposed test

A page qualifies when **all** hold:

1. its own stripped text is empty (a container, not a text)
2. no subpages, transcludes nothing (no other form already claimed it)
3. links **≥2** Main-namespace pages, excluding `वर्गः:`, `चित्रम्:`,
   `सञ्चिका:`, `अनुक्रमणिका:`, `पृष्ठम्:` prefixes
4. **every** link resolves to an existing page — one redlink disqualifies it,
   because a partial work is worse than none
5. **every** target has extracted text
6. no target is already part of another rolled-up work (no double-claiming)

Parts fold in link order, exactly as the other four forms do: one real file for
the work, and its parts not written separately.

## The open question — decide before building

Criterion 3 admits genre anthologies. Measured:

```
  0% stem-share   17 links   नाटकानि          "plays" — unrelated dramas
  0% stem-share    5 links   पद्यकाव्यानि      "verse poetry" — a genre index
 97% stem-share   37 links   तन्त्रालोकः       a real work, 37 āhnikas
100% stem-share   18 links   रसरत्नसमुच्चयः    a real work
```

Rolling up `नाटकानि` yields one "text" that is Abhijñānaśākuntala followed by
Uttararāmacarita followed by three more unrelated plays. **A fabricated work is
worse than an unlinked one.**

**A stem-share threshold looks like the fix and is not sufficient.**
`पूर्वमीमांसादर्शनम्` scores 0% while listing `मीमांसासूत्राणि`,
`शबरभाष्यम्`, `श्लोकवार्तिकम्` — the genuine constituent texts of the school,
sharing no prefix with the parent. A stem test rejects a real case and would
still need a whitelist beside it.

Three ways to resolve:

**(a) Whitelist by title, ~26 entries.** Explicit, auditable, no false
positives. Costs a list that goes stale as the wiki changes — but at 26 entries
reviewed once that is cheap, and a stale entry fails safe: the page simply
keeps no link.

**(b) Stem-share ≥ 80% plus a whitelist for exceptions.** Fewer manual entries,
but two mechanisms and a guessed threshold.

**(c) Ask the category.** A genre anthology is usually filed under a category
that is itself a genre. Structural rather than lexical, but needs its own
investigation and may not separate the cases cleanly.

**Recommendation: (a).** The population is 26, the risk worth avoiding is a
fabricated work, and an explicit list is the only option that cannot produce
one silently.

---

# Fix 2 — the 8 sub-5 KB items

Not really a fix: these are items whose "text" is a stub. `मुखपुटम्` is the
wiki's own main page (5 KB of navigation); `गणितसारसङ्ग्रहः` is 102 bytes.

Two options, and **the second is probably right**:

- extract them anyway, so every visible item has a link, and accept that a few
  links open onto near-nothing
- treat them the way image-only scans are now treated — below some floor, an
  item is not a text and should not be counted or shown as one

If the floor route is taken, pick the threshold from the data rather than
guessing: the gap between these 8 and the next-smallest real work is where it
belongs. Note that `मुखपुटम्` is arguably not a corpus item at all and might be
excluded by name regardless.

---

## What Sāgarasaṅgama needs

**Nothing, for either fix.** It derives its `lt` flag from each Atlas's
published `has_text`, and its visibility from `po`. Anything the Atlas starts
or stops pointing at follows automatically — as it did for all four handled
forms and for the scan-only change.
