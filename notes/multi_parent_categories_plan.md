# Plan: multi-parented categories (dynamic dual-display, not static alias)

## Background

Wikisource's category graph is not a tree: 15 categories in one crawl (2026-07)
are genuinely filed under two different parent categories. `cat_id()` used to
derive ids from title alone, so both occurrences collided on the same id,
corrupting id-keyed frontend lookups (sidebar selection/expansion, main-pane
ancestor breadcrumbs).

### Data-quality finding: the 15 confirmed multi-parented categories (2026-07 crawl)

Two clusters stand out:

**उपनिषत्/उपनिषदः cluster (6 cases)** — `प्रमुखोपनिषदः` ("principal/major
उपनिषद्s") is a curated subset that cross-cuts rather than strictly nests under
its own parent `उपनिषदः`:

| Category | Also filed under | In addition to |
|---|---|---|
| कठोपनिषत् | उपनिषदः > प्रमुखोपनिषदः | उपनिषदः |
| तैत्तिरीयोपनिषदत् | उपनिषदः > प्रमुखोपनिषदः | उपनिषदः |
| प्रश्नोपनिषत् | उपनिषदः | उपनिषदः > प्रमुखोपनिषदः |
| श्वेताश्वतरोपनिषत् | उपनिषदः | उपनिषदः > प्रमुखोपनिषदः |
| उपनिषद्भाष्यम् | दर्शनानि > आस्तिकदर्शनानि > उत्तरमीमांसादर्शनम् > अद्वैतम् | उपनिषदः |
| योगोपनिषदः | दर्शनानि > आस्तिकदर्शनानि > योगदर्शनम् | उपनिषदः |

**"रचनाः" (author-corpus) cluster under धर्मशास्त्रम् (5 cases)** — categories
named `<author>रचनाः` ("works of <author>") are filed both directly under the
umbrella `धर्मशास्त्रम्` and under that author's actual textual-genre home
elsewhere in the tree (Veda/Purāṇa/Vedāṅga):

| Category | Also filed under | In addition to |
|---|---|---|
| कण्वरचनाः | धर्मशास्त्रम् | वेदाः > ब्राह्मणम् |
| काश्यपरचनाः | धर्मशास्त्रम् | वेदाः > उपवेदाः > आयुर्वेदः |
| मार्कण्डेयरचनाः | धर्मशास्त्रम् | पुराणानि |
| लौगाक्षिरचनाः | धर्मशास्त्रम् | वेदाङ्गानि > शिक्षा |
| शाण्डिल्यरचनाः | धर्मशास्त्रम् | उपनिषदः |

**Remaining unclustered cases (4)**, each independent:

| Category | Also filed under | In addition to |
|---|---|---|
| उत्तरमीमांसादर्शनम् | दर्शनानि > वेदान्तग्रन्थाः | दर्शनानि > आस्तिकदर्शनानि |
| ऋग्वेदारण्यकम् | वेदाः > ऋग्वेदः | वेदाः > आरण्यकम् |
| ऋग्वेदब्राह्मणम् | वेदाः > ब्राह्मणम् | वेदाः > ऋग्वेदः |
| स्मृतयः | ग्रन्थाः (top-level) | वेदाङ्गानि > कल्पः |

The two occurrence positions listed per row are not ranked/canonical — per the
user, "neither is more real than the other, obviously." Re-derive this table
with a fresh crawl before trusting it long-term, since Wikisource's category
structure can change.

### Superseded first attempt (do not resurrect as-is)

First fix made ids path-hashed (good, keep this) but also made `build_skeleton()`
treat whichever occurrence the depth-first crawl reaches first as "canonical"
(real pages/subcats + real stats) and every later occurrence as a hollow
"alias" stub: empty children/pages, zero stats, `alias_of: <canonical id>`.
Frontend rendered alias stubs as a slim "→ see X" pointer in sidebar + main pane.

**User rejected this**: "neither is more real than the other, obviously." Crawl
order is an arbitrary implementation artifact, not a meaningful distinction, and
permanently privileging one occurrence over the other at scrape time is wrong.

## What's wanted instead (dynamic, decided at render time, not scrape time)

### Data model (scrape.py)

- Every occurrence of a multi-parented category gets **full real content**
  (pages + subcats), not a stub. No more zero-stat alias nodes.
- Every occurrence still needs a unique `id` (keep the path-hash `cat_id()` fix).
- Each occurrence of a shared category needs to know about its sibling
  occurrence(s) -- e.g. a `linked_ids: [other_id, ...]` field (or singular
  `linked_id` since only 2-way cases seen so far, but don't hardcode an
  assumption of exactly 2 -- Wikisource could have 3+ parents for some category).
- Rollup stats (bytes/count on ancestor categories) must NOT double-count a
  shared category's real content just because it's now duplicated in the raw
  tree structure. When computing a category's recursive stats, any descendant
  page that's reachable via more than one path within that same rollup must
  only be counted once. Practically: dedupe by page id (not by tree position)
  when summing bytes/count for any ancestor whose descendant set includes both
  occurrences of a shared category. The root total in particular must equal the
  true total distinct byte/page count on the actual site, not something inflated
  by the duplication.
- Determine, for each shared category, which occurrence is "first" in
  document/traversal order (this becomes purely a UI default -- see below --
  not a "canonical" designation). Likely just: whichever the existing
  depth-first crawl reaches first (same order as today), recorded as a simple
  ordering hint, e.g. `first_seen: true` on that occurrence and `false` on the
  rest, OR expose an explicit order index. Do not call this field anything with
  "canonical" or "alias" in the name -- both occurrences are equally real.

### Sidebar (`app.js` renderSidebarNode)

- Normal toggle dot/arrow (`·` / `▸` / `▾`) for ALL occurrences of a shared
  category -- remove the special `→` arrow and italic styling from the
  previous attempt entirely.
- Exactly one occurrence -- whichever comes first in top-to-bottom sidebar
  document order (i.e. respecting the actual rendered tree order, which follows
  the existing `first_seen`-style ordering) -- shows real stats
  `(N MB, N pages, date)`.
- Every OTHER occurrence of that same category shows literally `(duplicate)`
  in place of stats.
- Hovering over a `(duplicate)` row must highlight (visual treatment TBD --
  background flash or outline is fine) the sibling row(s) elsewhere in the
  sidebar where real stats are shown, so the user can see where its counterpart
  lives without clicking. Likely needs a shared `data-shared-group` attribute
  (or similar) on all sibling rows so a `mouseenter` handler can
  `querySelectorAll` and highlight them, un-highlighting on `mouseleave`.
- Clicking ANY occurrence (whether it's the one showing stats or the one
  showing "(duplicate)") simply sets `state.selectedCatId` to THAT SPECIFIC
  node's own id -- never redirect/alias the click to the other occurrence's id.
  Selection is per-occurrence.

### Main pane (`app.js` renderCategoryBlock / renderMain)

- Whichever occurrence is currently selected renders its full content (pages +
  subcats) in place, in its own ancestor breadcrumb context (per the existing
  breadcrumb feature).
- If the OTHER occurrence of that same category happens to also appear
  somewhere within what's currently rendered (this is inherently rare, since
  the main pane only renders the selected node's own ancestor chain + its own
  subtree -- the sibling occurrence would have to be a descendant of the
  selected node, or the selected node itself would need to be an ancestor of
  both, for it to show up at all) -- render it as a link/pointer rather than
  full content, with text making clear it's the same category shown elsewhere.
  Clicking that link flips `state.selectedCatId` to the other occurrence's id
  and re-renders the main pane rooted there.
- This "flip" behavior is the only remaining use for something like the old
  alias-link UI treatment, but now it's conditional/contextual (only shown when
  both occurrences happen to be visible in the same rendered view), not a
  permanent property of one occurrence baked into the data.

## Open implementation questions to resolve while coding (not yet decided)

- Exact shape of the "sibling ids" field on each occurrence (`linked_ids` list
  vs singular) -- lean toward a list to not assume exactly 2-way sharing.
- Exact mechanism/field name for "first in document order" (used only for
  sidebar stats-vs-"(duplicate)" placement, purely cosmetic/default, not a
  data-integrity concept) -- avoid "canonical"/"alias" naming per user feedback.
- Rollup dedup implementation: needs a global page-id -> byte-size map (already
  exists as `_size_cache` keyed by pageid in scrape.py) and, when computing a
  category's total bytes/count, walking all descendant pages via a
  dedup-by-pageid set rather than naively summing per-child stats bottom-up
  (bottom-up summing is exactly what would double count a shared descendant).
  May require restructuring `skeleton_to_json()`'s stats computation from
  "sum of children's precomputed stats" to "collect all descendant pageids
  into a set first, then size that set" for any node that has a shared
  category anywhere in its subtree. Simplest correct approach: always compute
  stats via a deduped pageid-set walk (not bottom-up sum), even for nodes with
  no sharing -- avoids having two different code paths.
- Need to decide the CSS/visual treatment for hover-highlight (not yet chosen).

## Also needs updating once implemented

- CLAUDE.md's "Key data shape" section and "Data-quality finding" section
  (currently describes the superseded alias-stub model; rewrite to describe
  the dynamic dual-occurrence model instead).
- Regenerate docs/data/tree.json via `python scrape.py` (should replay from
  `.api_cache/` quickly, no live network needed, per prior run in this session).
- docs/VERSION bump (already bumped to 0.2.0 for the sticky-header +
  breadcrumb work; may need another bump for this).

## Status as of writing this plan

Not yet started. Previous alias-stub implementation in scrape.py/app.js/
styles.css/CLAUDE.md/tree.json is sitting uncommitted in the working tree and
needs to be reworked (not necessarily fully discarded -- the id-collision fix
via path-hashed `cat_id()` should be kept; the alias/stub/zero-stat parts
should not).
