# Text-count inflation: resolved for the two dominant works

Opened 2026-07-30, resolved the same day. Picks up after `b579238` ("nest
subpages under the nearest existing ancestor, audit the rest").

## What this was

`असम्बद्धवर्गीकृतम्` reported **4,983 texts** against the central tree's 2,215,
mostly flat pages a reader would call chapters of one work. The question was
whether to infer structure from naming conventions or keep reporting what the
wiki literally says.

The first pass at this note framed inference as the only option and rejected it
as unsafe. That framing was wrong in a useful way: the two dominant families
don't need inference at all, because **their destination pages already exist on
the wiki**. Fixing them was a two-row allowlist, not a rule.

## What was actually found

Probing the 2026-07-01 dump directly:

**महाभारतम् — 2,315 chapters, `महाभारतम्-NN-<parva>-NNN`.** All 18 parva numbers
map to exactly one parva name (zero ambiguity), and all 18 `महाभारतम्/<parva>`
pages exist as real non-redirects. `महाभारतम्/आदिपर्व/००१` is a chapter someone
already converted to `/` form by hand, and the wiki carries 18 redirects from
the hyphen form to the slash form — the community already treats the two forms
as the same thing.

**ऋग्वेदः — 1,028 sūktas, `ऋग्वेदः सूक्तं M.S`.** 100% match the pattern, zero
exceptions. All 10 `ऋग्वेदः मण्डल M` destination pages exist. Per-maṇḍala counts
(191/43/62/58/87/75/104/103/114/191) reproduce the canonical saṃhitā exactly —
the pattern describes a fully regular reality rather than approximating a messy
one. The earlier worry that the सूक्तं tier had no page was misdirected: the
*maṇḍala* is the grouping level, and it's recoverable from the number before the
dot.

**The tail genuinely is irregular**, which is what makes the allowlist the right
shape. Below ~83 pages: stems that don't exist (`समराङ्गणसूत्रधार अध्याय`,
`दशक`), chapter *ranges* (`अष्टाङ्गसंग्रहः ... अध्याय १-५`), naive splits landing
mid-parenthetical (`सिद्धान्तकौमुदी (बालमनोरमा पूर्व २-२)`). Plus 2,544 flat
titles whose inferred stem coincidentally *is* a real page — `ऋग्वेदः देवतासूची`
is a standalone index sharing the `ऋग्वेदः` prefix, and is the control case the
implementation checks. A general rule buys ~100 pages in the tail and silently
mis-nests hundreds.

## What shipped

`FLAT_FAMILY_PATTERNS` in `pipeline/build_tree.py` — two rows, consulted only for
titles with no `/`, never synthesizing a parent. `check_flat_family_allowlist`
in `pipeline/audit.py` asserts each row is still live (dead row, fallback to
top-level, destination became a redirect). See CLAUDE.md's "The flat-family
allowlist" section for the bar a new row has to clear.

Result, applied retroactively across all 174 monthly snapshots:

| | before | after |
|---|---|---|
| असम्बद्धवर्गीकृतम् text_count | 4,983 | **2,231** |
| root text_count | 2,215 | **1,625** |
| महाभारतम् category | 593 texts | **4** |
| audit: separator families | 2,338 pages | **24** |
| audit: breadcrumb gaps | 1,589 pages | **561** |

Verified as pure re-parenting: distinct page/index ids 26,742 → 26,742 (zero
added, zero removed), and `content_bytes`/`transliterated_bytes` net to exactly
zero across root + orphan bucket — every byte that entered the central tree left
the bucket. Because the change is assembly-only, all 174 snapshots rebuilt from
the content cache in ~3 minutes.

> **Correction (2026-07-31).** This section originally claimed the historical
> trend shifted down "uniformly (−590/−592 per month) with **no step
> discontinuity**." That is **false**, and believing it is why the defect went
> unnoticed for a day. Measured per era against the last known-smooth series,
> `a63b21b` drops **IA months by −2,302** and **materialized months by −3,412**
> — a ~1,110-page gap that *is* a step discontinuity, visible as deep notches
> in the About page's Text Count chart. The uniform figure was computed against
> the current-month tree only and does not hold across the historical range.
>
> The mechanism: `_resolve_flat_family` never synthesizes a parent, so in months
> where the destination pages aren't found the family falls back to top-level
> and stays counted as texts. In IA-era months roughly 1,110 pages (≈ the
> ऋग्वेदः family) fail to nest; in materialized months they nest correctly. So
> **the IA months are the ones left with spurious text counts**, not the
> materialized ones. See `ia-dumps-resist-nesting.md` for the full bisect and
> the open question of why IA dumps resist nesting.

## What's left in the orphan bucket, and why it's a different problem

The remaining 2,231 texts are **not** a title-naming problem. The bucket's mass
is category connectivity:

| Pages | Cause |
|---|---|
| 1,444 | 10 `वर्गः:महाभारतम्/<parva>` categories with **no category tag at all** |
| 22 | 2 categories whose tags carry a stray `U+200E` (`ऋग्वेदः‎`, `रसरत्नसमुच्चयः‎`), creating phantom twins of real connected categories |
| 2,096 | 41 further categories genuinely disconnected from `वर्गसर्वस्वम्` |
| 2,696 | pages carrying no category at all |

Worth knowing about the first row: `वर्गः:महाभारतम्/सभापर्व` *renders* a
breadcrumb to `महाभारतम्` on the live wiki, because MediaWiki gives Category-
namespace titles the same `/` subpage breadcrumb as Main-namespace ones. That is
a title-structure relationship, entirely separate from `[[वर्गः:...]]` membership
— and the mirror's category graph is built purely from explicit tags. Applying
`_resolve_ancestor`-style `/` parenting to the Category namespace would follow
MediaWiki's own semantics and reconnect all 10, with no allowlist. It would also
produce a category hierarchy duplicating the page hierarchy those chapters now
live in, so it's worth thinking about what it's *for* before doing it.

The `U+200E` row is a small, mechanical, general fix (strip format characters
when normalizing category titles). The other two rows are real wiki gaps with no
mechanism to recover from — that's where the wiki-editing campaign applies, or a
further allowlist if it's ever worth it.

## Investigated and rejected, then shipped anyway: suppressing duplicate category listings

> **Status update (2026-07-31).** This section records a *rejection*, but
> `5c41f34` ("suppress duplicate flat listings of centrally-reachable
> subpages") shipped fix #2 the next day, keying it on central *reachability*
> so the two failure modes below are avoided: works whose nested form lives
> only in the orphan bucket keep their flat listing, and categories that
> suppression empties outright are pruned rather than left as dead ends. Read
> the analysis below as the constraints that shaped the shipped design, not as
> a live decision to avoid it. `5c41f34` was checked during the 2026-07-31
> bisect and does **not** contribute to the text_count discontinuity.


A page can appear twice in one view — once nested under its breadcrumb
ancestor, once listed flat under a category it tags directly. `आचारकाण्डः`
shows all 240 `गरुडपुराणम्/आचारकाण्डः/अध्यायः N` chapters flat, while the same
chapters are reachable by expanding `गरुडपुराणम्` → `गरुडपुराणम्/आचारकाण्डः`.
57 categories do this, 5,067 listings in total. All are `text_count: 0`, so
nothing is miscounted — it's purely display.

Two fixes were designed and both fail. **Do not retry either without reading
this.**

**1. Widen `process.py`'s suppression to any ancestor** (today it only checks
the *immediate* parent's tags). Measured: suppresses 63 listings across 4
categories, and does nothing for `आचारकाण्डः`. The tag chain is *disjoint* —
`गरुडपुराणम्` is tagged `गरुडपुराणम्`, `गरुडपुराणम्/आचारकाण्डः` is tagged
nothing at all, and the chapters are tagged `आचारकाण्डः`. No ancestor carries
the tag, so no ancestor walk can suppress them.

**2. Suppress any breadcrumb subpage whose work root is reachable elsewhere.**
Structurally correct (verified: zero categories gather subpages from more than
one work — all 57 are single-work, so no thematic grouping would be lost), but
it breaks discoverability two ways:

- **~20 categories would be left completely empty** — `आचारकाण्डः` (240),
  `श्रीमद्भागवत महापुराण` (320), `रामायणम्/उत्तरकाण्डम्` (111), the five
  `विष्णुपुराणम्` sections. The work root is filed in a *different* category,
  so nothing remains to list and the node becomes a dead end.
- **10 work roots aren't reachable from the category tree at all** —
  `विष्णुपुराणम्`, `वायुपुराणम्`, `गर्गसंहिता`, `जातकपारिजातः`. Their only
  categories are themselves orphaned, so the "redundant" flat listing is the
  **only** path to that content.

The lesson: the duplication is a *symptom* of the disconnected-category
problem above, not an independent display bug. The flat listings are load-
bearing exactly where the category graph is broken. Fix connectivity upstream
first; only then does suppressing duplicates become safe.

## To re-measure

```
python -m pipeline.audit dump/1_current_format_live/sawikisource-<date>.xml
```

For orphan-bucket figures, walk `docs/data/tree.json`'s असम्बद्धवर्गीकृतम् child.
Note its direct `children` are categories (the 53 above); its 2,696 loose pages
sit in its own `pages` list.
