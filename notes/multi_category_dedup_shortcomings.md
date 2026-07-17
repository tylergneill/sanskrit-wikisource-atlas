# Multi-category page/index-item dedup: remaining shortcomings

Follow-up to the fix in this commit (pipeline/process.py: pages/index-items
tagged under >1 category now each build a full real node instead of being
suppressed via page-pointer/index-item-pointer at all but one occurrence, and
recompute_stats_dedup() sums the distinct set of reachable page/index-item ids
per category rather than naively summing children's precomputed stats).

## Open bug: `stats: null` at a second occurrence of the same page

Confirmed live in a real regenerated `docs2/data/tree2.json`: the same page
(by id, e.g. `page:वेदव्यासस्मृतिः`) has correct, populated `stats` at one
category occurrence but `stats: null` at another. Example traced:

```
('ग्रन्थाः (धर्मशास्त्राणि च)', 'धर्मशास्त्रम्') -> stats populated correctly
('ग्रन्थाः (धर्मशास्त्राणि च)', 'वेदाङ्गानि', 'कल्पः', 'स्मृतयः') -> stats: None
```

Same pattern hit multiple pages under स्मृतयः specifically: आङ्गिरसस्मृतिः,
गौतमस्मृतिः, वसिष्ठस्मृतिः, वेदव्यासस्मृतिः, शङ्खसंहिता, शातातपसंहिता all show
`stats: None` at their स्मृतयः occurrence while showing correct stats at their
other (e.g. धर्मशास्त्रम्) occurrence. Confirmed in the frontend screenshot too
(these rows render with the multi-cat search icon but no size/date at all).

`build_page_node()` (pipeline/process.py) is called fresh, once per category
that directly tags a page -- it does NOT share/reuse a single dict instance
across occurrences (each call builds its own `own_stats`/`rolled`/`node`
dict). So a `None` at one occurrence and populated data at another cannot be
explained by simple identity-sharing; something in `build_page_node` or its
`content_index.main_sizes` lookup path is producing an empty/falsy result for
specific (page, category) combinations, not for the page overall. Root cause
NOT YET IDENTIFIED -- was mid-investigation when this got shelved to get the
partial fix committed. Next step: instrument/step through build_page_node for
वेदव्यासस्मृतिः specifically across both its call sites (from धर्मशास्त्रम् and
from स्मृतयः in build_category's `for page_title in sorted(pages_by_cat.get(title, []))`
loop) and diff exactly what differs -- candidates to check first:
- `content_index.main_sizes.get(main_node.title)` returning `None` on the
  second lookup for some non-obvious reason (cache invalidation? key
  collision?).
- `main_node.record` being unexpectedly `None` on one path (main_node itself
  is shared/looked-up from the same `main_nodes` dict both times, so this
  seems unlikely, but not yet ruled out).
- Something in `recompute_stats_dedup`'s memoization incorrectly attaching a
  category-level memoized empty result to a page-level dict (id collision
  between a category id and a page id seems very unlikely given the `cat:`
  vs `page:` prefixing, but worth double-checking the memo dict never gets
  cross-contaminated).

This needs to be fixed before the multi-category dedup work can be considered
complete -- right now the *listing* is correct (page appears at every real
category tag, per the original goal) but the *stats* are not reliably correct
at every occurrence, which undermines the whole point of showing them
independently rather than as pointers.

## Also flagged for follow-up (from earlier review, not yet verified against
## the final implementation in this commit)

1. **Ancestor rollup dedup correctness** -- verified in isolation (a small
   synthetic 5-node graph, not the real corpus) that stats/count rollups
   correctly dedupe a category/page reachable via two converging branches,
   not just at root, because pointer children are skipped when summing. This
   was checked against the *original* pointer-based scheme (before this
   commit's rewrite to recompute_stats_dedup). Worth re-verifying the same
   property against the final recompute_stats_dedup implementation and real
   data now that the null-stats bug above is understood/fixed -- the null
   values could plausibly also be corrupting rollup sums silently (a `None`
   stats dict flowing into `_merge_stats` would presumably crash rather than
   silently under-count, but this hasn't been confirmed either way).

2. **Stale docstring reference** -- pipeline/process.py's module docstring
   used to reference a `reachable_content()` helper that was never actually
   implemented (the real dedup was the emitted_ids/DFS-first-wins scheme).
   This was corrected as part of this commit's docstring updates, but worth a
   final read-through of the whole module docstring against the actual
   recompute_stats_dedup implementation to make sure nothing else drifted
   during the rewrite.
