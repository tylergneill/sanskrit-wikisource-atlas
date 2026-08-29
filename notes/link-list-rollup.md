# Link-list pages: should they roll up?

**Status: specified, not built.** Decide the open question at the bottom before
implementing.

## What this is about

Four ways a work is assembled are already handled (see `pipeline/process.py`
and rivulet's `extract/wikisource/text_extractor.py`):

| form | how the parts are found |
| --- | --- |
| subpage children | `main_nodes`, the redirect-resolved subpage tree |
| redirect-renamed children | same — `वाल्मीकिरामायणम्` holds `रामायणम्/…` |
| transcluded scan leaves | `resolve_transcluded_leaves` |
| untranscluded Index items | `untranscluded_leaves_by_index` |

Wikisource fulltext coverage is **96%** (3655/3805 items in Sāgarasaṅgama's
search index carry a `txt` link).

A fifth form is unhandled: a page that is a header plus a **bulleted list of
`[[wikilinks]]`** to separately-titled works. `तन्त्रालोकः` is 1420 bytes of
wikitext linking 37 āhnikas, each of which is its own Main page with its own
text. It strips to zero content, so it has no fulltext and no link — while the
Atlas still lists it as an item.

This is **142 items, ~4% of the collection.** Their text is already extracted
under the linked titles; what is missing is the work-level entry point.

## Why this one is different

The other four forms rest on a structural claim of ownership:

- a subpage title (`Work/Chapter`) says "I am part of Work"
- `<pages index="X.djvu" from=1 to=205/>` says "my content is X's leaves 1–205"

**A wikilink says nothing.** `[[X]]` means "see X" as readily as "X is my
chapter 3". Rolling up every link-list would concatenate genre anthologies into
fake works. So this needs a test, and the test needs to be conservative.

## Measured shape of the 142

Against the 2026-08-01 dump:

```
142   zero-byte misses
 56     have any wikilinks to Main pages at all
 32     where every link resolves to an existing page
 26     also have ≥2 links and every target has extracted text
```

Rejections among the 56: 30 have `<2` links, 21 link to at least one redlink,
3 link to a page with no text.

So a strict test admits **26 works**, taking coverage 96% → ~97%. That is the
honest ceiling here — this is a tail, not a breakthrough.

## The proposed test

A page qualifies as a link-list work when **all** hold:

1. its own stripped text is empty (it is a container, not a text)
2. it has no subpages and transcludes nothing (the other four forms did not
   already claim it)
3. it links **≥2** Main-namespace pages, excluding `वर्गः:`, `चित्रम्:`,
   `सञ्चिका:`, `अनुक्रमणिका:`, `पृष्ठम्:` prefixes
4. **every** link resolves to an existing page — one redlink disqualifies the
   page, because a partial work is worse than none
5. **every** target has extracted text
6. no target is already a part of some other rolled-up work (no double-claiming)

Parts are then folded in link order, exactly as the other four forms do: the
work gets one real file, and its parts are not written separately.

## The open question — read before building

Criterion 3 admits genre anthologies. Measured examples:

```
  0% stem-share   17 links   नाटकानि          "plays" — unrelated dramas
  0% stem-share    5 links   पद्यकाव्यानि      "verse poetry" — a genre index
 97% stem-share   37 links   तन्त्रालोकः       a real work, 37 āhnikas
100% stem-share   18 links   रसरत्नसमुच्चयः    a real work
```

Rolling up `नाटकानि` would produce a single "text" that is Abhijñānaśākuntala
followed by Uttararāmacarita followed by three more unrelated plays. That is a
fabricated work and worse than leaving it unlinked.

**A stem-share threshold looks like the fix and is not sufficient.**
`पूर्वमीमांसादर्शनम्` scores 0% while listing `मीमांसासूत्राणि`,
`शबरभाष्यम्`, `श्लोकवार्तिकम्` … — the genuine constituent texts of the
school, sharing no prefix with the parent. A stem test rejects a real case and
would still need a whitelist for it.

Three ways to resolve, in order of my preference:

**(a) Whitelist by title, ~26 entries.** Explicit, auditable, no false
positives. Costs a per-work list that goes stale as the wiki changes — but at
26 entries reviewed once, that is cheap, and a stale entry fails safe (the page
simply keeps no link).

**(b) Stem-share ≥ 80% plus a whitelist for the exceptions.** Fewer manual
entries, but two mechanisms instead of one, and the threshold is a guess.

**(c) Ask the category.** A genre anthology is usually filed under a category
that is itself a genre. Structural rather than lexical, but needs its own
investigation and may not separate the cases cleanly.

**Recommendation: (a).** The population is 26, the risk of a fabricated work is
the thing actually worth avoiding, and an explicit list is the only option that
cannot produce one silently.

## What Sāgarasaṅgama needs

**Nothing.** It derives its `lt` flag from each Atlas's published `has_text`,
so anything the Atlas starts pointing at appears in search with no change
there. This has held across all four existing forms.
