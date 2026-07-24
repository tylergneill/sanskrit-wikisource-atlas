# Orphan bucket top-level categories vs. `find_orphaned_categories`: why they diverge, and a detection gap this surfaced

Investigated 2026-07-24 while comparing the About page's audit finding
"Categories never filed under any parent (orphaned)" (`pipeline/audit.py`'s
`find_orphaned_categories`, 60 entries on the 2026-07-01 dump) against the
top-level category children of the orphan bucket itself
(असम्बद्धवर्गीकृतम्, `pipeline/process.py`, 55 entries same dump).

## The two lists overlap only 21/94 titles -- expected, not a bug

They're built by genuinely different mechanisms:

- **`find_orphaned_categories`** (`pipeline/audit.py` -> `orphaned_category_titles`
  in `pipeline/build_tree.py`): scans `CategoryGraph.nodes` -- built entirely
  from real Category-namespace *page records* and the `[[वर्गः:...]]` parent
  links found in *their own* wikitext (`build_category_graph`). Reports every
  node with zero parents.
- **Orphan bucket top level** (`pipeline/process.py` lines ~485-517): walks
  every unreachable-from-root Main page/Index item, looks at *that page's
  own* direct category tags, and treats each such tag as a fresh "root" to
  crawl as its own subtree. A category becomes a bucket-top-level entry
  because some orphaned *page* happened to tag it directly -- not because it
  is itself parentless. If that category has its own parent tag, but the
  parent is *also* unreachable from root, the child still surfaces at the
  bucket's top level (whichever page's tag discovered it first), while its
  parent shows up as a *sibling* top-level entry too, or nested if crawled
  in the right order -- there's no guarantee the bucket's top level reflects
  real parent/child structure at all.

So "in bucket only" (34 titles: `Rig Veda`, `Sanskrit`, `विष्णुपुराणम्`,
`यजुर्वेद`, `हितोपदेशः`, `संहिता`, etc.) does NOT mean "has a parent tag that
fails to reach root," as I first assumed and stated -- checked directly
against `CategoryGraph.nodes` and **none of these six spot-checked titles
are category-graph nodes at all**. They were never named as a category
anywhere `build_category_graph` looks (not a real Category-namespace page,
not referenced as a parent/child link *within* another category's own
wikitext).

## The actual explanation: red-link categories tagged only from Main-namespace pages

`build_category_graph` only discovers a category node two ways:
1. A real Category-namespace page exists for it.
2. Some OTHER category's own `[[वर्गः:...]]` link names it as parent/child.

A category tag that appears **only** on a Main-namespace (or Index-namespace)
page -- e.g. a page carrying `[[वर्गः:विष्णुपुराणम्]]` where
`वर्गः:विष्णुपुराणम्` was never created as a real page and is never
mentioned by any other category -- never gets a graph node at all. It's
invisible to:
- `find_orphaned_categories` (only iterates `graph.nodes`)
- `find_redlink_categories` (`sorted(t for t, n in graph.nodes.items() if
  n.record is None)` -- also only iterates `graph.nodes`, so a title that
  never became a node in the first place isn't "None record", it's just
  absent)

It only becomes visible at all via `process.py`'s orphan-bucket logic,
which is the only code path that scans Main-namespace pages' own direct
tags independently of the category graph.

## Why this matters: `find_redlink_categories` has a blind spot

"Categories referenced but never created (red links)" is meant to catch
exactly this kind of problem (a category tag pointing at nothing) -- but it
currently only catches red-link tags that appear on *another category's*
wikitext, not red-link tags that appear directly on a Main/Index page. A
Main page tagged with a category that (a) was never created as a real page
and (b) is never referenced by any other category is the same underlying
defect (fix: create the category page, or fix the tag) but is currently
under-reported -- it shows up only indirectly, buried as an orphan-bucket
top-level entry, with no dedicated audit finding calling it out as a
red-link specifically.

**Not yet fixed.** Fixing `find_redlink_categories` (or a new check) to
also scan Main/Index-namespace direct category tags -- not just categories
referenced from within other categories' own text -- would close this gap.
Would need a title-source input `find_redlink_categories` doesn't currently
take (main_nodes'/content_index's direct-tag maps, not just the
`CategoryGraph`), so it's a signature change, not a one-line fix. See
`notes/pipeline_upgrade_plan.md` and `pipeline/audit.py`'s existing six
checks for the pattern to follow.
