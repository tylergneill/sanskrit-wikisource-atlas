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

---

## 3. The flat-family allowlist: structure inferred, but only twice

**Decision.** Two works encode chapter hierarchy with a separator that
breadcrumb logic cannot see, at a scale that dominates the corpus's text
count. These get an explicit allowlist entry each:

| Pattern | Destination | Pages |
|---|---|---|
| `महाभारतम्-NN-<parva>-NNN` | `महाभारतम्/<parva>` | 2,315 |
| `ऋग्वेदः सूक्तं M.S` | `ऋग्वेदः मण्डल M` | 1,028 |

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
