# Interpretive decisions

Every number this mirror shows rests on a judgment call about what counts as
one text, where a page belongs, and which of several true statements to
display. Wikisource does not answer those questions for us — its category
graph is hand-maintained, incomplete, and internally inconsistent — so the
pipeline decides, and those decisions are what this file records.

`CLAUDE.md` documents *how* each mechanism works, for someone changing the
code. This file documents *why that choice and not another*, for someone
who wants to know whether to trust a figure or how to describe it publicly.
It is the intended source for any About-page prose on the subject; write the
reasoning here once, then draw from it, rather than maintaining two copies.

Figures throughout are against the **2026-07-01 dump**. They will drift.
Treat them as illustrating magnitude, not as live values — the site itself
is authoritative for current counts.

---

## The recurring problem: two filing systems that don't agree

Almost everything below descends from one fact. sa.wikisource files content
two independent ways at once:

1. **By category tag** — a page carries `[[वर्गः:...]]` markup naming
   categories it belongs to. Editors write these by hand, per page.
2. **By title breadcrumb** — a page titled `A/B/C` is understood to be a
   child of `A/B`, which is a child of `A`. This is structural, implied by
   the title alone, and nobody has to maintain it.

Neither system knows about the other. A work can be beautifully nested by
title and tagged into nothing; another can be tagged consistently and have
completely flat titles. When both exist and disagree, the same page can
arrive in the tree by two routes, and we have to decide which to show.

Most decisions in this file are a particular answer to that.

---

## 1. `text_count` vs `count`: what "one text" means

**Decision.** Two different totals, shown for different purposes.
`count` = every distinct page and Index item, each subpage counted
separately. `text_count` = only *top-level* texts — a page with no
breadcrumb parent, or an Index item.

**Why.** A 240-chapter purāṇa is 240 pages but one text. Reporting 240
overstates how much there is to read; reporting 1 hides real content. Both
numbers are true and neither alone is honest, so both exist, and the sidebar
shows `text_count` because it matches what a reader would call one work.

**Current figures.** Whole corpus 26,742 items / 3,783 texts. Central,
well-categorized tree 22,788 / 1,625.

**Consequence worth knowing.** `text_count` is only as good as breadcrumb
parenting. Every failure to resolve a parent inflates it, because a stranded
chapter counts as its own top-level text. This makes it a rough
data-quality signal as much as a content measure.

---

## 2. Root's headline stats exclude the orphan bucket

**Decision.** Content unreachable from the root category `वर्गसर्वस्वम्` is
collected into an artificial category `असम्बद्धवर्गीकृतम्` ("improperly
categorized"). It is browsable and its own stats are real, but root's
headline figures deliberately leave it out. The true total including it is
carried separately as `all_stats`.

**Why.** Root is meant to describe the organized corpus — what a reader
browsing categories can actually find. Folding in 2,231 texts that are
reachable by no category path would misrepresent the collection as better
organized than it is.

**Why it's not hidden.** The orphan bucket is 4,271 items / 2,231 texts —
too much to suppress, and it is exactly the material most in need of
upstream fixing. Hiding it would defeat one of the project's purposes.

**Judgment being made.** That "reachable from the root category" is a
reasonable proxy for "properly filed." It is a proxy. A perfectly good text
sits in the orphan bucket if nobody tagged it.

### Corollary: rollup cannot bridge the orphan boundary

Worth stating explicitly, because it is the mechanism behind every
historical `text_count` wobble that has ever been chased in this repo.

Ancestor rollups dedup by *distinct page id*: a page reachable by two paths
is counted once, at whichever ancestor those paths first converge. And
`text_count` is assigned per page by one rule only —
`text_count=1 if main_node.parent_title is None else 0` (`process.py`) —
i.e. by the **title breadcrumb**, never by category placement.

Those two facts are fine on their own. The problem is when a single work is
split *across* the boundary: part of it centrally reachable, part in the
orphan bucket. Then the two halves have no common ancestor to converge at,
so rollup cannot merge them, and the work is effectively counted twice —
once in root's totals and once in the bucket's.

The practical consequence: **anything that changes which categories reach
the root will move `text_count`**, even though no page's own text-ness ever
changed. A dump missing a handful of category pages strands whole works in
the bucket, and the count moves by hundreds. That is why byte totals and
`count` stay perfectly smooth across such a month while `text_count`
notches — bytes and page counts don't care which bucket a page landed in.

Known live instance: the 2015-01 and 2015-05 materialized snapshots contain
104 Category-namespace pages where their neighbours have 139 (counted
directly in the reconstructed XML). ~35 missing categories strand enough
content to move `text_count` by roughly −370 in each of those two months.
Deliberately **not** fixed — see the About page's Snapshots section. The
cause is understood, bounded to two months out of 174, and the alternatives
(interpolating the points, or reworking category reconstruction in
`materialize_snapshots.py`) are both worse than a documented artifact.

### The 2020–2025 orphan plateau is real history, not an artifact

Worth recording because it looks exactly like the artifact above and is not.
The orphan bucket's share of the corpus runs:

| Period | Orphan share |
|---|---|
| before 2015 | 0% |
| 2016 – 2020-01 | ~13–19% |
| **2020-07 – 2025-01** | **~50%** |
| 2025-07 – present | ~14% |

Across 2020-01 → 2020-07 the central tree *falls* from 19,168 to 11,422 items
while the corpus keeps growing. The cause is upstream: **`पुराणानि` lost its
link to the root category**, taking its whole subtree with it — 32 categories
and roughly 7,700 pages — and it was re-filed by 2025-07.

The tell that this is genuine and not a reconstruction failure: Category-
namespace *page* counts keep climbing right through the window (241 → 285),
so nothing is missing from the dumps. What changes is connectivity —
categories reachable from the root drop 158 → 134, hold there for five years,
then recover to 168. The pages were always present and always counted; they
were simply not reachable by category descent, which is precisely what the
orphan bucket is for.

So the mirror is reporting this correctly. Anyone reading the trend charts
should know the mid-period orphan mass is a real, five-year gap in
sa.wikisource's own category maintenance, not a defect in this pipeline.

---

## 3. The flat-family allowlist: structure inferred, but only twice

**Decision.** Two works encode chapter hierarchy with a separator that
breadcrumb logic cannot see, at a scale that dominates the corpus's text
count. These get an explicit allowlist entry each:

| Pattern | Destination | Pages |
|---|---|---|
| `महाभारतम्-NN-<parva>-NNN` | `महाभारतम्/<parva>` | 2,315 |
| `ऋग्वेद[ः:] सूक्तं M.S` | `ऋग्वेदः मण्डल M` | 1,028 |

**A pattern must cover historical spellings, not just today's.** The
ऋग्वेदः row originally matched only the visarga stem `ऋग्वेदः` (U+0903),
which is correct for the current dump. But those 1,028 pages were titled
with an ASCII colon `ऋग्वेद:` (U+003A) until a mass rename in 2017-08, so in
every backfilled month before that the pattern matched nothing, all 1,028
sūktas fell back to top-level, and each was counted as a standalone text —
a large, invisible inflation across a third of the history. The destination
carried the visarga in both eras, so only the child side needed widening.
Post-rename the colon titles survive as redirects, which `text_count`
already ignores, so accepting both spellings changes nothing in the modern
era. `check_flat_family_allowlist` cannot catch this class of problem: it
validates against the current dump only, where the row was always healthy.

**Why an allowlist and not a rule.** A general "infer structure from
separators" rule would be wrong far more often than right. 2,544 flat titles
have an inferred stem that coincidentally *is* a real page without being its
parent (`ऋग्वेदः देवतासूची` is a standalone index that merely shares the
`ऋग्वेदः` prefix). Below these two families the shapes stop being regular:
stems that don't exist, chapter *ranges* rather than chapters, naive splits
landing mid-parenthetical. A rule loose enough to catch these two would
silently mis-nest hundreds — and the tree would look plausible while being
wrong, which is the worst failure mode available.

**The bar for adding a row.** Destination already exists on-wiki as a real
non-redirect page; pattern matches the family exhaustively with no
exceptions; resulting child counts match the work's real structure. Both
current rows clear it (ऋग्वेदः reproduces the canonical per-maṇḍala counts
exactly). For महाभारतम् the wiki *already asserts* the relationship — 18
redirects from hyphen form to slash form, and one chapter an editor
converted by hand — so this transcribes an existing claim rather than
inventing one.

**Safety property.** A parent is never synthesized. If a destination stops
existing, its pages fall back to top-level rather than nesting under
something fabricated, and the audit reports it loudly.

---

## 4. Duplicate flat listings: suppress only when the work is centrally reachable

**Decision.** When a subpage would be listed flat under a category it tags
directly, *and* the same page is already browsable nested under its work
root somewhere in the central tree, drop the flat listing. Keyed on whether
the work root is reachable from `वर्गसर्वस्वम्` — not on tags.

**Effect.** 2,574 listings suppressed, 2,817 kept, 21 emptied categories
pruned. Display only: no stats move, all 26,742 distinct items remain
reachable and searchable.

**Why reachability and not tags.** Two approaches were measured and
rejected first:

- *Widen the existing tag test to any ancestor.* Suppresses only 63
  listings and does nothing for the motivating case, because in the
  Garuḍapurāṇa no ancestor carries the tag at all.
- *Suppress every breadcrumb subpage unconditionally.* Structurally
  tempting, but it strands content — 10 work roots are not reachable from
  the category tree, and for those the "redundant" flat listing is the
  **only** path in.

**The self-correcting property, which is the point.** The rule reads live
wiki structure rather than a hardcoded list. Tag an orphaned work root into
a central category upstream, and its duplicate listings suppress themselves
on the next run with no code change. Decisions that expire on their own are
strongly preferred here over decisions that need maintaining.

---

## 5. Multi-filed pages are shown at every filing, not deduplicated

**Decision.** A page tagged into two categories appears under both, each
occurrence independently selectable with real stats, linked by a "see also"
pointer. Which occurrence physically holds the content in the JSON is an
artifact of build order and carries no meaning.

**Why.** `गौतमतन्त्रम्` is genuinely both a tantra and a Gautama work.
Picking one filing as canonical would assert something the wiki does not.
777 pages have two legitimately disjoint category paths like this.

**Interaction with stats.** Dedup happens at rollup: a page reachable by two
paths is counted once, at whichever ancestor the paths first converge. So
duplicate *display* never inflates totals.

---

## 6. One reconstruction method for all history, not the best-available source

**Decision.** Every historical month is **materialized** — reconstructed
from `sawikisource-latest-pages-meta-history.xml.bz2` by taking, for each
page, its newest revision at or before that month's cutoff. Only the current
era (from `LEGACY_CUTOVER`) uses a real live export. Internet Archive dumps
are no longer used, even for the many months where a genuine archived
snapshot exists.

**Why.** A real archived dump records the titles pages bore *at that date*.
The reconstruction records the titles they bear *today*. Both are internally
coherent; they are not mutually comparable. Since `text_count` is derived
from title breadcrumbs (§1), the same corpus counted from the two sources
differs by hundreds of texts — so a series that switches source mid-history
shows steps at every switch that look like corpus events and are not. The
old series switched sources a dozen times.

Given that a boundary is unavoidable somewhere, one consistent method for
all of history is worth more than each month's most authentic individual
source. A trend chart's job is comparability across time.

**What this costs.** Materialization's own deviations now apply everywhere
rather than only in gaps (see `materialize_snapshots.py`):

- Pages **hard-deleted** before the meta-history dump was taken are absent
  from every month, so each month is a lower bound and deletion events
  largely stop being visible. Measured cost: of 5,199 removals the old
  mixed-source series reported, only ~106 (2%) were genuine deletions —
  the rest were renames and re-filings counted as remove+add pairs. The
  "removed" column loses little real signal.
- Titles are **as of the dump's vintage**, so a work reorganized in 2020
  appears reorganized in 2013 too. This is the deliberate trade: today's
  structural understanding applied uniformly backward, rather than each
  month's own worse organization.
- Error grows with distance backward. Near the dump's vintage the
  reconstruction is nearly exact, which is why the materialized→live
  handoff is clean.

**What was given up, and how to get it back.** 76 real archived dumps exist
(2011-09 .. 2022-05) and are the only source for period-accurate titles and
for pages later deleted. `notes/internet-archive-dumps.md` records exactly
which months are available, the gaps, and how to fetch one — the knowledge is
kept even though the fetching code was removed.

**Judgment being made.** That a consistent series which is slightly wrong in
a known direction is more useful than an accurate-per-month series whose
month-to-month deltas are dominated by source artifacts. For a chart about
growth over time, comparability is the property that matters.

---

## Known limitation: duplication this does not resolve

2,094 pages are still listed more than once within the central tree. They
are not one problem:

| Kind | Count | Status |
|---|---|---|
| Filed in both a deep category and an ancestor of it | 1,317 | Tractable, unaddressed |
| Genuinely filed in two unrelated categories | 777 | **Correct as-is** — see §5 |

The 1,317 are the real remaining target: the deeper path is strictly more
informative, so the shallow one could be dropped safely. A rule was drafted
and measured for this and turned out to catch only 60 listings, because the
duplication is nested-vs-nested rather than direct-vs-direct — the drafted
rule only ever removed direct listings. A correct rule has not been written.

### Worked example: the Viṣṇupurāṇa — RESOLVED UPSTREAM, 2026-07-31

The best evidence so far that §4's self-correcting property works in
practice. Kept in full because the diagnosis is reusable, not because the
problem is still live.

**The symptom.** One page — chapter 1 of the fourth aṃśa, 6,232 bytes —
appeared three times in the tree:

1. **Because of its own tag.** Its wikitext carries
   `[[वर्गः:श्रीविष्णुपुराणम्-चतुर्थांशः]]`, so it was listed in that category.
2. **Because of its title.** Named `विष्णुपुराणम्/चतुर्थांशः/अध्यायः १`, the
   slashes make it a child of `विष्णुपुराणम्/चतुर्थांशः`, whose own tag is
   `[[वर्गः:श्रीविष्णुपुराणम्]]` — so it appeared nested there too. Nobody
   filed the chapter there; it arrived because its parent is there.
3. **The same nesting again**, under the orphan-bucket copy of the work.

Once by tag, twice by title — the two filing systems from the top of this
file, disagreeing.

**The diagnosis.** Of the work's 133 pages, 132 carried tags naming
categories that exist. Exactly one did not: the work's **top** page,
`विष्णुपुराणम्`, ended with `[[वर्गः:विष्णुपुराणम्]]` — a category with no
description page, containing only that single page. A redlink.

§4's rule walks to the top of the title chain and asks whether *that* page is
tagged somewhere centrally reachable. Finding a dead tag, it concluded the
work was unanchored, and kept the duplicate listings as possibly the only way
in. **The rule was behaving correctly on broken input.**

The `श्री` discrepancy is the fingerprint: categories were named *with* it,
pages *without* it, and the one tag bridging the two was written in the
page-side spelling — pointing at a category nobody ever created.

**The fix, made on-wiki 2026-07-31 (revid 417928).** One line, one page:

```
[[वर्गः:विष्णुपुराणम्]]   →   [[वर्गः:श्रीविष्णुपुराणम्]]
```

**The result, with no code change whatsoever:**

| | before | after |
|---|---|---|
| occurrences of the chapter | 3 | 1 |
| categories rendered | 215 | 208 |
| distinct items in corpus | 26,742 | 26,742 |

Seven categories stopped being rendered — the six aṃśa categories plus the
dead orphan cluster — not because content vanished, but because their
chapters became reachable nested under a properly anchored work root, so §4
collapsed the duplicates. The work moved from the orphan bucket into the
central tree (root `text_count` 1,625 → 1,626); `all_stats` was unchanged,
correctly, since nothing entered or left the corpus.

**Why this was left to an upstream fix rather than patched in the pipeline.**

1. **The defect was upstream.** Code papering over it would have left the
   wiki broken *and* hidden the evidence, when surfacing such problems is
   part of why this project exists.
2. **A looser rule would have guessed.** Catching this in code means deciding
   a deeper category and a shallower one show "the same thing." True here;
   false for the 777 genuine multi-filings (§5). Too loose, and real
   memberships vanish invisibly.

**Correction to an earlier claim.** Prior notes (and the implementation
plan) stated the central `श्रीविष्णुपुराणम्` was "missing the sixth aṃśa
entirely," offered as a reason both copies had to stay visible. That was
never verified and is **false**: `श्रीविष्णुपुराणम्-षष्टांशः` exists and has
8 pages tagged into it. The duplication was purely a filing defect, with no
content gap underneath it.

**The real lesson: the diagnosis was far too hard.** The fix was one line.
Getting from what the interface showed to knowing *which* line took an
extended investigation — reading the assembled tree, classifying duplicate
paths, walking the title chain by hand, and finally querying the live wiki
API to confirm the category was a redlink. A reader looking at the site
could see *that* something was duplicated, but had no path to *why*, and no
way to know that one specific page's tag was the cause.

This asymmetry — trivial fix, expensive diagnosis — is the actual barrier to
the self-correcting property being useful. §4's rule only pays off when
someone makes the upstream edit, and nobody makes an edit they can't locate.
Two concrete gaps to close:

- **Redlink category tags are invisible in the UI.** A page tagged into a
  category that doesn't exist is a specific, machine-detectable defect
  (`tag not in graph.nodes`). It is the root cause here and is likely
  behind other orphan clusters. The audit should list these outright:
  page, dead tag, and the nearest existing category with a similar name.
- **The orphan bucket says "unreachable" without saying why.** When a work
  lands there because its root's only tag is dead, that is a one-sentence
  explanation the pipeline already has enough information to emit.

Until those exist, treat this worked example as a *method*: when a work is
duplicated between the central tree and the orphan bucket, check the tag on
the work's **top** page first. That single check would have short-circuited
most of the investigation described above.

**This is not a one-off.** Detecting the pattern is a two-line query
(`tag not in graph.nodes`), and against the 2026-07-01 dump it finds:

- **26** distinct nonexistent categories referenced by Main pages
- **167** pages carrying at least one dead tag
- **123** of those on *top-level* pages — the same shape as विष्णुपुराणम्,
  where the damage propagates to every descendant

Ranked by descendants affected, the largest are:

| top-level page | dead tag | descendants |
|---|---|---|
| `गर्गसंहिता` | `संहिता` | 279 |
| `कृष्‍णयजुर्वेदः` | `कृष्‍णयजुर्वेदः` | 58 |
| `ऋग्वेदः` | `ऋग्वेदः‎` | 23 |
| `रघुवंशम्` | `मालविकाग्निमित्रम्` | 21 |
| `बुद्धचरितम्` | `बौद्धवाङ्मयम्` | 14 |

Note `गर्गसंहिता` — §4 protects its 61 flat listings precisely because its
root is unanchored, and this is why.

Note also that two of these are **invisible-character bugs**, and both are
unambiguous:

| dead tag | hidden character | strip it and you get | exists? |
|---|---|---|---|
| `कृष्‍णयजुर्वेदः` | `U+200D` ZERO WIDTH JOINER | `कृष्णयजुर्वेदः` | **yes** |
| `ऋग्वेदः‎` | `U+200E` LEFT-TO-RIGHT MARK | `ऋग्वेदः` | **yes** |

In both cases removing the invisible character yields a category that
actually exists, so the intended target is not in doubt and the fix needs no
editorial judgment. These are undiagnosable by eye at any level of effort —
the tag renders identically to a working one. Precisely the case for
machine detection, and a strong argument for the audit addition above.

See `notes/wikisource-editing-plan.md` for the broader upstream campaign.

---

## Standing principles

Extracted from the decisions above, for judging future ones:

1. **Never synthesize structure.** Fall back to top-level and report it.
   A wrong tree that looks right is worse than a flat one that looks flat.
2. **Prefer decisions that expire.** Rules reading live wiki structure
   self-correct when the wiki is fixed; hardcoded lists must be maintained
   and silently rot.
3. **Suppress display, never content.** Every suppression here removes a
   duplicate route to something reachable elsewhere. No item should ever
   become unreachable or unsearchable — verify this explicitly, by count.
4. **Surface upstream defects rather than hiding them.** The mirror is
   partly an instrument for improving the source.
5. **Both numbers when both are true.** `count` and `text_count`, root
   stats and `all_stats` — where one figure would mislead, show the pair.

---

## Appendix: Viṣṇupurāṇa reference data

Compiled 2026-07-31 to support a separate investigation into a July 2026
attempt to extract this work from Wikisource into another text collection —
specifically, whether the filing defects above interfered with that attempt,
and whether the extractor obtained the sixth aṃśa. Recorded here because it
is expensive to re-derive and easy to get subtly wrong.

### The work is complete, and always was

126 chapter pages across six aṃśas (134 pages total including index pages),
**no gaps in any aṃśa** — every chapter from 1 to the maximum is present:

| aṃśa | chapters | tagged into its category | content bytes |
|---|---|---|---|
| प्रथमांशः | 22 | 22 | 406,447 |
| द्वितीयांशः | 16 | 16 | 204,759 |
| तृतीयांशः | 18 | 18 | 213,882 |
| चतुर्थांशः | 24 | 24 | 332,192 |
| पञ्चमांशः | 38 | 38 | 392,484 |
| षष्टांशः | **8** | 8 | 125,151 |

**The sixth aṃśa's 8 chapters are correct, not truncated.** Two independent
confirmations: the canonical Viṣṇupurāṇa's sixth aṃśa has 8 adhyāyas, and
the wiki's own index page `विष्णुपुराणम्/षष्टांशः` lists exactly
`अध्यायः १` through `अध्यायः ८` and stops (verified against live wikitext,
2026-07-31). Earlier notes treated "only 8" as evidence of a gap; it is not.
Anyone extracting this work should have obtained 8 chapters there — 8 is the
success condition, not a red flag.

### State during July 2026, when the extraction happened

The extractor was working against the **broken** wiki: the dead-tag defect
was live from 2016-01-19 until it was fixed 2026-07-31T12:03:29Z. In the
2026-07-01 dump the top page `विष्णुपुराणम्` still carried
`[[वर्गः:विष्णुपुराणम्]]`.

What that means concretely for a category-driven extraction:

- The work's top page was in a **redlink category containing only itself**.
  A crawler starting from `वर्गः:पुराणानि` and descending categories would
  reach `वर्गः:श्रीविष्णुपुराणम्` and its six aṃśa subcategories, but would
  **not** reach the work through `विष्णुपुराणम्`.
- The six aṃśa categories were correctly populated the whole time (the
  table above holds for the 2026-07-01 dump), so a category-driven crawl
  that found `श्रीविष्णुपुराणम्` should have gotten **all six aṃśas
  including the eighth-chapter sixth**.
- A crawl driven from the **top page's own category** would have found
  essentially nothing, since that category held one page.
- A crawl driven by **title prefix** (`विष्णुपुराणम्/`) would have gotten
  everything, since the breadcrumb titles were always intact.

So the discriminating question for the investigation is *which entry point
the extractor used*. Missing content, or getting five aṃśas but not the
sixth, would not be explained by the defects documented here — the sixth was
as reachable as the other five by every route. A total miss of the work, or
finding it only as an orphan, is consistent with a category-driven crawl
hitting the dead tag.

### Naming trap

Pages are titled **without** `श्री` (`विष्णुपुराणम्/चतुर्थांशः/अध्यायः १`);
categories are named **with** it (`श्रीविष्णुपुराणम्-चतुर्थांशः`). Note also
that the categories use a **hyphen**, not a slash. An extraction tool
matching page titles against category names, or assuming the category name
predicts the page path, would fail on both counts. This mismatch is old:
the 2011 revisions of the top page linked to `श्रीविष्णुपुराणम्-प्रथमांशः`
style titles, which were later moved to the slash form, leaving redirects.

### Revision history of the top page (the defect's whole life)

```
2011-11-12  Sbblr0803            first version, links श्रीविष्णुपुराणम्-* titles
2012-02-23  Sandeep V Kulkarni   added [[Category:पुराणानि]]
2016-01-19  Udit Sharma          removed पुराणानि, added विष्णुपुराणम्   <-- DEFECT
2017-06-29  Puranastudy
2018-08-11  Puranastudy
2019-06-03  Puranastudy          redirect work, श्रीविष्णुपुराणम् mentioned
2019-06-04  Puranastudy
2023-08-05  अनुनाद सिंह
2026-07-31  (fix)                विष्णुपुराणम् -> श्रीविष्णुपुराणम्
```

Introduced in two consecutive edits 13 seconds apart on 2016-01-19, and live
for **ten and a half years**. Four subsequent editors touched the page
without noticing — consistent with the "undiagnosable by eye" point above,
since a redlink category renders as an ordinary-looking link at the bottom
of the page.

### Provenance signal: the 2019-06-03 batch

All six aṃśa index pages were created within about two minutes of each other
on 2019-06-03 (20:17:24 through 20:20:14) by user `Puranastudy`, which looks
like a scripted or batch upload rather than hand editing. Last-edit years
across the work's 134 pages: 2019 (59), 2020 (2), 2021 (20), 2022 (31),
2023 (6), 2024 (2), 2026 (14). Useful if the investigation needs to
distinguish the wiki's own import history from the later extraction.

### Caution when using historical snapshots for this

`dump/_backfill_snapshots/` has 174 monthly snapshots (2012-02 onward), but
**do not read early ones as evidence of when content appeared.** Materialized
months are reconstructed from a 2026-vintage meta-history dump, so a naive
query reports all six aṃśas present in 2012-02 while 2016-01 and 2018-01
show zero — mutually contradictory. The 2019-06 onward figures are
consistent with the revision history and can be trusted; earlier ones cannot.
See CLAUDE.md's "Dump vintage" section for why.

Also note the 2026-07-01 content cache was hand-patched with the 2026-07-31
fix (see the worked example above); the unmodified original is not in git.
The 2026-08-01 dump supersedes both.
