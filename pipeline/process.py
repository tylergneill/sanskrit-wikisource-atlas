"""
Process stage: runs parse_dump/build_tree/transclusion/content_size in
sequence and assembles their outputs (namespace records, Main subpage tree,
Category digraph, transclusion map, content-size stats) into a single JSON
tree for the frontend.

New schema (deliberately not identical to the old scraper's tree.json --
see notes/sawikisource-scraper-spec.md and the 2026-07 branch decision to
adapt the frontend rather than force new concepts into the old shape):

Node (category):
  { id, type: "category", title, children: [Node],
    pages: [PageNode], index_items: [IndexItemNode], stats }

Node (category-pointer): a second+ filing of a category already emitted
elsewhere in the tree (multi-parent category, see build_tree.CategoryGraph).
Appears inline among its parent's own `children`, alongside real category
nodes -- there is no separate list of pointers.
  { id, type: "category-pointer", title, points_to: <id>, stats }

PageNode (Main-namespace page, filed into this category via its own direct
[[वर्गः:...]] tag):
  { id, type: "page", title, url, stats, subpages: [PageNode] }
  subpages come from the Main-namespace tree (build_tree.MainPageNode),
  nested the same way the old schema nested them. A breadcrumb-subpage
  (title split on "/") normally only appears nested inside its parent's
  PageNode, but if it carries its own direct category tag NOT also carried
  by its immediate parent, it additionally gets its own full, independently
  reachable PageNode under that category too -- same "no pointer/skip logic,
  dedup happens in stats rollup" treatment build_category already gives
  multi-tagged top-level pages (see build_category's page_jsons loop and
  notes/wikisource-editing-plan.md, "Silent subpage category divergence").

Node (page-pointer): a second+ filing of a page already emitted elsewhere in
the tree (a page tagged with >1 category directly -- same multi-filing
concept as category-pointer, just for pages instead of categories).
  { id, type: "page-pointer", title, url, points_to: <id> }
  No stats/subpages of its own; resolve via points_to to the occurrence that
  has them (mirrors category-pointer's resolution, see docs/app.js's
  resolveContent).

IndexItemNode (Index-namespace item with ZERO transclusion anywhere in
Main-namespace content -- i.e. raw/unpublished OCR, per
transclusion.is_transcluded):
  { id, type: "index-item", title, url, stats }
  Never expandable into individual पृष्ठम्:Title/N (scanned-leaf) rows --
  those are only ever summed into this node's own stats, never listed (see
  compute_page_ns_rollup below and notes/sawikisource-scraper-spec.md,
  "Untranscluded Index items").

Node (index-item-pointer): a second+ filing of an Index item already emitted
elsewhere in the tree. Same shape/resolution as page-pointer.
  { id, type: "index-item-pointer", title, url, points_to: <id> }

stats: { raw_bytes, content_bytes, transliterated_bytes, count, last_changed }
  count = number of distinct Main pages + Index items reachable from this
  node. Dedup is enforced at build time by build_category(): the first DFS
  occurrence of a category/page/Index-item builds real content and folds its
  stats into every ancestor's rollup; every later occurrence anywhere else in
  the tree is emitted as a *-pointer node instead and is skipped when summing
  ancestor stats -- so a page/category reachable via two paths is still
  counted exactly once, at whichever ancestor its two paths first converge
  (not only at root).

Orphan bucket (असम्बद्धवर्गीकृतम्, see ORPHAN_BUCKET_TITLE): an ordinary
category node, appended as a sibling of the real category tree under root,
holding every Main page and untranscluded Index item unreachable from
वर्गसर्वस्वम् by category descent -- either zero category tags, or tags that
only point to categories themselves never filed under any reachable parent
(orphaned category roots, walked in as real subtrees the same way the main
tree is). Root's own headline stats deliberately exclude this bucket's
totals (matches scrape.py's अवर्गीकृतम्/OCR-bucket convention) -- it's
listed and browsable, just not counted as part of the "central" corpus
size. See build_tree_json for the mechanics.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pipeline.build_tree import (
    CategoryGraph,
    MainPageNode,
    build_category_graph,
    build_main_tree,
    refile_category,
)
from pipeline.content_size import (
    ContentSizeResult,
    build_template_index,
    compute_content_sizes_parallel,
)
from pipeline.parse_dump import DumpIndex, PageRecord, category_links, is_excluded_category, parse_dump
from pipeline.transclusion import build_transclusion_map, direct_categories, is_transcluded

Stats = dict  # {raw_bytes, content_bytes, transliterated_bytes, count, last_changed}

ROOT_CATEGORY_TITLE = "वर्गसर्वस्वम्"


def page_url(title: str) -> str:
    from urllib.parse import quote
    return "https://sa.wikisource.org/wiki/" + quote(title.replace(" ", "_"))


def index_url(title: str, index_ns_name: str) -> str:
    from urllib.parse import quote
    return "https://sa.wikisource.org/wiki/" + quote((index_ns_name + ":" + title).replace(" ", "_"))


def category_url(title: str, category_ns_name: str) -> str:
    from urllib.parse import quote
    return "https://sa.wikisource.org/wiki/" + quote((category_ns_name + ":" + title).replace(" ", "_"))


@dataclass
class ContentIndex:
    """Precomputed per-page/per-index-item content-size results, keyed by
    title, so the tree-assembly walk below doesn't recompute expansion for
    a page it visits more than once (e.g. via category-pointer dedup)."""
    main_sizes: dict[str, ContentSizeResult]
    index_sizes: dict[str, ContentSizeResult]
    main_categories: dict[str, set[str]]  # Main page title -> its own direct category tags
    index_categories: dict[str, set[str]]  # Index item bare title -> its own direct category tags
    index_timestamps: dict[str, str]  # Index item bare title -> its own revision timestamp
    index_page_rollup: dict[str, Stats]  # Index item bare title -> summed stats over its untranscluded पृष्ठम्:Title/N children


def _owning_index_title(page_title: str, page_ns_name: str) -> str | None:
    """पृष्ठम्:<IndexBareTitle>/<N> -> <IndexBareTitle>, or None if the title
    doesn't match the Title/N convention at all (rare malformed case)."""
    prefix = page_ns_name + ":"
    if not page_title.startswith(prefix):
        return None
    bare = page_title[len(prefix):]
    if "/" not in bare:
        return None
    return bare.rsplit("/", 1)[0]


def compute_page_ns_rollup(
    dump_index: DumpIndex,
    template_index: dict[str, str],
    known_titles: set[str] | None,
    cat_ns_name: str,
    transliterate: bool,
    workers: int | None,
    transclusion_map: dict[str, set[str]],
) -> dict[str, Stats]:
    """Rolls up size/date stats from पृष्ठम्:Title/N (scanned-leaf) records
    onto their owning Index item, for untranscluded Index items only --
    Index is the organizing principle pre-transclusion, so individual leaves
    are never listed/browsed, only summed into one stat on the Index item
    (see notes/sawikisource-scraper-spec.md, "Untranscluded Index items").
    Restricting to untranscluded items keeps this cheap: a transcluded
    Index's Page: content is superseded by real Main-namespace text and
    dropped from display anyway, so there's no reason to pay for its
    (often large) template-expansion + transliteration cost here.
    """
    page_ns_id = dump_index.page_ns_id()
    # Page (104) is the ProofreadPage extension -- absent from early dumps
    # (e.g. 2011-10, before the extension was enabled on sawikisource). No
    # namespace means no scanned-leaf records to roll up, not an error.
    page_ns_name = dump_index.namespaces[page_ns_id] if page_ns_id is not None else ""
    all_page_records = dump_index.pages_by_ns.get(page_ns_id, []) if page_ns_id is not None else []

    relevant_records = [
        rec for rec in all_page_records
        if (owner := _owning_index_title(rec.title, page_ns_name)) is not None
        and not is_transcluded(owner, transclusion_map)
    ]

    sizes = compute_content_sizes_parallel(
        relevant_records, template_index, known_titles, cat_ns_name,
        transliterate=transliterate, workers=workers, progress_label="content size: Page (scan) leaves",
    )

    rollup: dict[str, Stats] = {}
    for rec in relevant_records:
        owner = _owning_index_title(rec.title, page_ns_name)
        size = sizes[rec.title]
        current = rollup.get(owner) or _empty_stats()
        rollup[owner] = _merge_stats(current, _stats_dict(
            size.raw_wikitext_bytes, size.content_bytes, size.transliterated_bytes,
            0,  # leaves don't each count as a separate "work" -- only the Index item itself does
            rec.timestamp,
        ))
    return rollup


def compute_all_content_sizes(
    dump_index: DumpIndex,
    transliterate: bool,
    transclusion_map: dict[str, set[str]],
    workers: int | None = None,
) -> ContentIndex:
    template_ns_name = dump_index.namespaces[dump_index.template_ns_id()]
    template_records = dump_index.pages_by_ns.get(dump_index.template_ns_id(), [])
    template_index = build_template_index(template_records, template_ns_name)

    cat_ns_name = dump_index.namespaces[dump_index.category_ns_id()]
    known_titles = {r.title for r in dump_index.pages_by_ns[0]}

    main_records = dump_index.pages_by_ns[0]
    main_sizes = compute_content_sizes_parallel(
        main_records, template_index, known_titles, cat_ns_name,
        transliterate=transliterate, workers=workers, progress_label="content size: Main pages",
    )
    main_categories = {rec.title: direct_categories(rec, cat_ns_name) for rec in main_records}

    index_ns_id = dump_index.index_ns_id()
    # Index (106) is the ProofreadPage extension -- absent from early dumps
    # (e.g. 2011-10, before the extension was enabled on sawikisource). No
    # namespace means no Index items, not an error.
    index_records = dump_index.pages_by_ns.get(index_ns_id, []) if index_ns_id is not None else []
    # keyed by bare title (namespace prefix stripped) below, but the pool
    # itself keys by record.title (the full "अनुक्रमणिका:..." title) --
    # remap after the fact.
    index_sizes_by_full_title = compute_content_sizes_parallel(
        index_records, template_index, known_titles, cat_ns_name,
        transliterate=transliterate, workers=workers, progress_label="content size: Index items",
    )

    def bare(title: str) -> str:
        return title.split(":", 1)[1].strip() if ":" in title else title.strip()

    index_sizes = {bare(rec.title): index_sizes_by_full_title[rec.title] for rec in index_records}
    index_categories = {bare(rec.title): direct_categories(rec, cat_ns_name) for rec in index_records}
    index_timestamps = {bare(rec.title): rec.timestamp for rec in index_records}

    index_page_rollup = compute_page_ns_rollup(
        dump_index, template_index, known_titles, cat_ns_name,
        transliterate, workers, transclusion_map,
    )

    return ContentIndex(
        main_sizes=main_sizes,
        main_categories=main_categories,
        index_sizes=index_sizes,
        index_categories=index_categories,
        index_timestamps=index_timestamps,
        index_page_rollup=index_page_rollup,
    )


def _stats_dict(raw: int, content: int, translit: int, count: int, last_changed: str, text_count: int = 0) -> dict:
    return {
        "raw_bytes": raw,
        "content_bytes": content,
        "transliterated_bytes": translit,
        "count": count,
        "text_count": text_count,
        "last_changed": last_changed,
    }


def _empty_stats() -> dict:
    return _stats_dict(0, 0, 0, 0, "")


def _merge_stats(a: dict, b: dict) -> dict:
    return _stats_dict(
        a["raw_bytes"] + b["raw_bytes"],
        a["content_bytes"] + b["content_bytes"],
        a["transliterated_bytes"] + b["transliterated_bytes"],
        a["count"] + b["count"],
        max(a["last_changed"], b["last_changed"]) if a["last_changed"] or b["last_changed"] else "",
        a.get("text_count", 0) + b.get("text_count", 0),
    )


def build_page_node(
    main_node: MainPageNode,
    owning_cat_id: str,
    content_index: ContentIndex,
) -> tuple[dict, dict]:
    """Returns (json_node, own_stats) where own_stats covers this page alone
    (not its subpages). `stats` on the returned node is set to own_stats too,
    NOT a subpage-inclusive rollup -- a subpage can now also be independently
    filed under a category tag of its own (see build_category's page_jsons
    loop, "Silent subpage category divergence"), so a single rolled total
    computed here would double-count that subpage wherever both its nested
    and independently-filed occurrences are reachable from a common
    ancestor. recompute_stats_dedup (below) walks `subpages` itself, dedups
    every page id (at any depth) against every OTHER occurrence anywhere in
    the tree, and overwrites `stats` with the real rolled total -- same
    bottom-up, dedup-by-id treatment categories already get."""
    size = content_index.main_sizes.get(main_node.title)
    last_changed = main_node.record.timestamp
    own_stats = _stats_dict(
        size.raw_wikitext_bytes if size else 0,
        size.content_bytes if size else 0,
        size.transliterated_bytes if size else 0,
        1,
        last_changed,
        # A subpage (breadcrumb title, e.g. "टीका/१") isn't its own separate
        # "text" for browsing purposes -- it's part of its top-level parent's
        # work, even when it also gets independently filed here under its own
        # category tag (see "Silent subpage category divergence" above). Only
        # a true top-level title (no "/" parent) counts toward text_count.
        text_count=1 if main_node.parent_title is None else 0,
    )

    subpage_jsons = []
    for child in main_node.children:
        child_json, _ = build_page_node(child, owning_cat_id, content_index)
        subpage_jsons.append(child_json)

    node = {
        "id": f"page:{main_node.title}",
        "type": "page",
        "title": main_node.title,
        "url": page_url(main_node.title),
        "stats": own_stats,
        "subpages": subpage_jsons,
    }
    return node, own_stats


def build_index_item_node(bare_title: str, content_index: ContentIndex, index_ns_name: str) -> dict:
    size = content_index.index_sizes.get(bare_title)
    rec_timestamp = content_index.index_timestamps.get(bare_title, "")
    own_stats = _stats_dict(
        size.raw_wikitext_bytes if size else 0,
        size.content_bytes if size else 0,
        size.transliterated_bytes if size else 0,
        1,
        rec_timestamp,
        text_count=1,
    )
    # The Index page's own wikitext is just proofreading-status scaffolding
    # (near-zero content by design) -- the real scanned/proofread text lives
    # on its पृष्ठम्:Title/N children, rolled up separately (see
    # compute_page_ns_rollup). Merge that in so stats reflect the actual
    # scan, not just the Index page shell.
    page_rollup = content_index.index_page_rollup.get(bare_title)
    stats = _merge_stats(own_stats, page_rollup) if page_rollup else own_stats
    return {
        "id": f"index-item:{bare_title}",
        "type": "index-item",
        "title": bare_title,
        "url": index_url(bare_title, index_ns_name),
        "stats": stats,
    }


def build_category_membership_maps(content_index: ContentIndex) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """category title -> list of Main page titles / Index bare titles directly
    tagged with that category. Excluded/maintenance categories are dropped
    from the tag lists themselves (a page tagged [[वर्गः:निष्कासनाय]] doesn't
    file it under a node that will never exist in the graph)."""
    pages_by_cat: dict[str, list[str]] = {}
    for title, cats in content_index.main_categories.items():
        for cat in cats:
            if is_excluded_category(cat):
                continue
            pages_by_cat.setdefault(cat, []).append(title)

    index_items_by_cat: dict[str, list[str]] = {}
    for title, cats in content_index.index_categories.items():
        for cat in cats:
            if is_excluded_category(cat):
                continue
            index_items_by_cat.setdefault(cat, []).append(title)

    return pages_by_cat, index_items_by_cat


ORPHAN_BUCKET_TITLE = "असम्बद्धवर्गीकृतम्"


def build_tree_json(
    dump_index: DumpIndex,
    graph: CategoryGraph,
    main_nodes: dict[str, MainPageNode],
    transclusion_map: dict[str, set[str]],
    content_index: ContentIndex,
) -> dict:
    index_ns_id = dump_index.index_ns_id()
    # Same ProofreadPage-extension caveat as above: only used to build
    # index_url() links, and only ever dereferenced against real Index
    # items, which won't exist if the namespace itself doesn't.
    index_ns_name = dump_index.namespaces[index_ns_id] if index_ns_id is not None else ""
    category_ns_name = dump_index.namespaces[dump_index.category_ns_id()]
    pages_by_cat, index_items_by_cat = build_category_membership_maps(content_index)

    emitted_ids: dict[str, str] = {}  # category title -> id of its first (real) emission

    def cat_id(title: str) -> str:
        return "cat:" + title

    def build_category(title: str) -> dict:
        node_id = cat_id(title)
        if title in emitted_ids:
            return {
                "id": node_id + ":pointer",
                "type": "category-pointer",
                "title": title,
                "url": category_url(title, category_ns_name),
                "points_to": emitted_ids[title],
                "stats": None,  # filled in by rollup pass
            }
        emitted_ids[title] = node_id

        cat_node = graph.nodes.get(title)
        children = []
        if cat_node is not None:
            for child_title in sorted(cat_node.children):
                children.append(build_category(child_title))

        # Every category that directly tags a page/Index item builds and shows
        # its own full, real node -- a page tagged into 2+ categories is a
        # genuinely true member of each, not a redundant duplicate (unlike a
        # multi-parented *category*, whose descendant content really is
        # identical everywhere). No pointer/skip logic at this level; dedup
        # for ancestor rollups happens in a separate pass below
        # (recompute_stats_dedup) that sums over the *distinct* set of page/
        # Index-item ids reachable from a node, so a page counted here and
        # again at a sibling category is still only counted once wherever
        # their paths converge.
        #
        # A breadcrumb-subpage (main_node.parent_title is not None) is
        # normally only reachable by nesting inside its parent's own page
        # node (see build_page_node's recursion below), not filed here
        # directly -- its own direct tags are otherwise silently dropped for
        # filing purposes (see notes/wikisource-editing-plan.md, "Silent
        # subpage category divergence"). Surface it here too, exactly like a
        # top-level multi-tagged page, but only for tags its immediate
        # parent doesn't already carry -- a tag the parent also carries adds
        # no new discoverability, since browsing the parent's node already
        # surfaces this subpage via its nested `subpages` list.
        page_jsons = []
        for page_title in sorted(pages_by_cat.get(title, [])):
            main_node = main_nodes.get(page_title)
            if main_node is None:
                continue  # not a Main record
            if main_node.parent_title is not None:
                parent_tags = content_index.main_categories.get(main_node.parent_title, set())
                if title in parent_tags:
                    continue  # already discoverable via the parent's own filing under this category
            page_json, _ = build_page_node(main_node, node_id, content_index)
            page_jsons.append(page_json)

        index_jsons = []
        for bare_title in sorted(index_items_by_cat.get(title, [])):
            if is_transcluded(bare_title, transclusion_map):
                continue  # published elsewhere in Main -- drop the raw Index item per spec
            index_jsons.append(build_index_item_node(bare_title, content_index, index_ns_name))

        return {
            "id": node_id,
            "type": "category",
            "title": title,
            "url": category_url(title, category_ns_name),
            "children": children,
            "pages": page_jsons,
            "index_items": index_jsons,
            "stats": None,  # filled in by recompute_stats_dedup below
        }

    root = build_category(graph.root_title)

    # Content unreachable from root by category descent: a page/Index item
    # whose only category tag(s) are themselves never filed under any parent
    # reachable from वर्गसर्वस्वम् (an orphaned category -- e.g. a whole
    # sub-tree that exists on the wiki but was never linked in), or that
    # carries no category tag at all. build_category() above already walked
    # every category reachable from root, so emitted_ids now IS the
    # reachable set -- same trick scrape.py's build_orphan_bucket used with
    # its seen_cats dict. Reusing build_category for orphan-category roots
    # means a category shared between two orphan clusters (or one that's
    # simply a deeper, not-yet-visited part of an orphan tree) gets the same
    # category-pointer/dedup handling for free.
    #
    # A page/Index item is reachable iff at least one of its direct tags is
    # itself reachable (in emitted_ids) -- unlike the old page-pointer scheme,
    # there's no "first-claimed-it" bookkeeping to consult here anymore, since
    # every reachable category independently re-emits its own direct tags.
    orphan_main_titles = sorted(t for t, n in main_nodes.items() if n.parent_title is None)
    orphan_index_titles = sorted(
        t for t in content_index.index_categories if not is_transcluded(t, transclusion_map)
    )

    orphan_cat_roots: dict[str, None] = {}  # ordered set of category titles to crawl as fresh roots
    orphan_zero_cat_main: list[str] = []
    orphan_zero_cat_index: list[str] = []
    for t in orphan_main_titles:
        cats = [c for c in content_index.main_categories.get(t, set()) if not is_excluded_category(c)]
        if any(c in emitted_ids for c in cats):
            continue  # reachable via at least one real tag -- already emitted above
        if cats:
            for c in cats:
                orphan_cat_roots.setdefault(c, None)
        else:
            orphan_zero_cat_main.append(t)
    for t in orphan_index_titles:
        cats = [c for c in content_index.index_categories.get(t, set()) if not is_excluded_category(c)]
        if any(c in emitted_ids for c in cats):
            continue
        if cats:
            for c in cats:
                orphan_cat_roots.setdefault(c, None)
        else:
            orphan_zero_cat_index.append(t)

    orphan_children = []
    for orphan_root_title in orphan_cat_roots:
        if orphan_root_title in emitted_ids:
            continue  # already swept in by an earlier orphan root this same loop
        orphan_children.append(build_category(orphan_root_title))
    orphan_children.sort(key=lambda n: n["title"])

    orphan_page_jsons = []
    for t in orphan_zero_cat_main:
        main_node = main_nodes[t]
        page_json, _ = build_page_node(main_node, cat_id(ORPHAN_BUCKET_TITLE), content_index)
        orphan_page_jsons.append(page_json)

    orphan_index_jsons = [
        build_index_item_node(t, content_index, index_ns_name) for t in orphan_zero_cat_index
    ]

    orphan_bucket = None
    if orphan_children or orphan_page_jsons or orphan_index_jsons:
        orphan_bucket = {
            "id": cat_id(ORPHAN_BUCKET_TITLE),
            "type": "category",
            "title": ORPHAN_BUCKET_TITLE,
            "children": orphan_children,
            "pages": orphan_page_jsons,
            "index_items": orphan_index_jsons,
            "stats": None,  # filled in below
        }
        root["children"].append(orphan_bucket)

    # Compute every category's stats bottom-up as the sum over the *distinct*
    # set of page/Index-item ids reachable from it -- not a naive sum of
    # children's stats, which would double-count a page/Index item filed
    # directly under two categories that both appear beneath this node (see
    # build_category above: every reachable category now independently
    # re-emits its own direct tags in full, on purpose, so this pass is the
    # only place dedup happens). Mirrors old scrape.py's attach_stats.
    # Memoized by node id since a category-pointer's target subtree, or a
    # category shared by two orphan-root sweeps, can be reached more than once.
    by_id: dict[str, dict] = {}

    def index_by_id(node: dict) -> None:
        by_id[node["id"]] = node
        for ch in node.get("children", []):
            index_by_id(ch)

    index_by_id(root)  # orphan_bucket is already among root["children"] by this point

    memo: dict[str, tuple[dict, dict, dict]] = {}

    def rewrite_page_subtree(node: dict) -> None:
        """Write each already-memoized node's correct stats into THIS
        occurrence's own dict object, recursing through `subpages` -- used on
        a cache hit (see recompute_page_dedup) where an entire duplicate
        subtree (e.g. a top-level page independently filed under 2+
        categories, each a fresh build_page_node() call -- see
        build_category's page_jsons loop) needs its stats fields corrected
        without redoing the O(n) computation, which the memo already has."""
        node["stats"] = memo[node["id"]][0]
        for sp in node.get("subpages", []):
            rewrite_page_subtree(sp)

    def recompute_page_dedup(node: dict) -> tuple[dict, dict]:
        """Returns (stats, page_stats_by_id) for the distinct set of page ids
        reachable from page node `node` -- itself plus every subpage, at any
        depth. A subpage can be reached two ways: nested here, or as its own
        independently-filed occurrence elsewhere in the tree (see
        build_category's page_jsons loop) -- memoized by id (shared with
        recompute_stats_dedup's memo) so the O(n) computation only happens
        once per id; every later occurrence (a physically different dict
        object, since build_page_node builds a fresh subtree per call) still
        needs its own stats fields corrected via rewrite_page_subtree, since
        skipping the computation must not mean skipping the write-back."""
        node_id = node["id"]
        if node_id in memo:
            stats, page_stats, _ = memo[node_id]
            rewrite_page_subtree(node)
            return stats, page_stats

        page_stats: dict[str, dict] = {node_id: node["stats"]}
        for sp in node.get("subpages", []):
            _, sp_page_stats = recompute_page_dedup(sp)
            for pid, s in sp_page_stats.items():
                page_stats.setdefault(pid, s)

        stats = _empty_stats()
        for s in page_stats.values():
            stats = _merge_stats(stats, s)

        node["stats"] = stats
        memo[node_id] = (stats, page_stats, {})
        return stats, page_stats

    def recompute_stats_dedup(node: dict) -> tuple[dict, dict, dict]:
        """Returns (stats, page_stats_by_id, index_stats_by_id) for the distinct
        set of page/Index-item ids reachable from `node`, each mapped to its own
        (already-correct, per-page) stats dict."""
        node_id = node["id"]
        if node_id in memo:
            return memo[node_id]

        if node["type"] == "category-pointer":
            target = by_id.get(node["points_to"])
            result = recompute_stats_dedup(target) if target else (_empty_stats(), {}, {})
            node["stats"] = result[0]
            memo[node_id] = result
            return result

        page_stats: dict[str, dict] = {}
        for p in node.get("pages", []):
            _, p_page_stats = recompute_page_dedup(p)
            for pid, s in p_page_stats.items():
                page_stats.setdefault(pid, s)
        index_stats: dict[str, dict] = {it["id"]: it["stats"] for it in node.get("index_items", [])}

        for child in node.get("children", []):
            _, child_page_stats, child_index_stats = recompute_stats_dedup(child)
            for pid, s in child_page_stats.items():
                page_stats.setdefault(pid, s)
            for iid, s in child_index_stats.items():
                index_stats.setdefault(iid, s)

        stats = _empty_stats()
        for s in page_stats.values():
            stats = _merge_stats(stats, s)
        for s in index_stats.values():
            stats = _merge_stats(stats, s)

        node["stats"] = stats
        result = (stats, page_stats, index_stats)
        memo[node_id] = result
        return result

    recompute_stats_dedup(root)  # recurses into orphan_bucket too, as one of root's children

    # वर्गसर्वस्वम् (the literal MediaWiki category root) isn't a useful node to
    # show readers -- once the junk siblings are excluded (see
    # EXCLUDED_CATEGORIES) and धर्मशास्त्रम् is folded into ग्रन्थाः (see
    # refile_category in main()), root's only *category-tree* child is
    # ग्रन्थाः, which just adds an extra meaningless click. Splice ग्रन्थाः's
    # own contents up to root directly, same as scrape.py did (it crawled
    # starting *at* ग्रन्थाः and never emitted it as a node at all) -- but
    # keep any OTHER real root children as siblings of ग्रन्थाः's contents
    # (currently just the orphan bucket, असम्बद्धवर्गीकृतम्, appended above;
    # scrape.py kept its equivalent अवर्गीकृतम्/OCR buckets as siblings of
    # the ग्रन्थाः-rooted tree the same way).
    granth = next((c for c in root["children"] if c["title"] == "ग्रन्थाः" and c["type"] == "category"), None)
    if granth is not None:
        other_siblings = [c for c in root["children"] if c is not granth]
        root = {
            "id": "root",
            "type": "category",
            "title": "ग्रन्थाः (धर्मशास्त्राणि च)",
            "children": granth["children"] + other_siblings,
            "pages": granth["pages"],
            "index_items": granth["index_items"],
            # Root's headline stats intentionally cover only the central,
            # well-categorized tree (ग्रन्थाः) -- same convention scrape.py
            # used (attach_stats ran on the central tree before अवर्गीकृतम्/
            # OCR buckets were appended as siblings). The orphan bucket's
            # own stats are still real and shown on its own node.
            "stats": granth["stats"],
        }

    return {"root": root}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml_path", type=Path, nargs="?", help="path to the uncompressed dump XML")
    parser.add_argument("--out", type=Path, default=Path("docs/data/tree.json"),
                         help="output path (default: docs/data/tree.json, the frontend's data dir)")
    parser.add_argument("--no-transliterate", action="store_true",
                         help="skip skrutable transliteration (faster, for quick iteration)")
    parser.add_argument("--workers", type=int, default=None,
                         help="worker processes for content-size computation (default: os.cpu_count())")
    args = parser.parse_args()

    run_start = time.time()

    xml_path = args.xml_path
    if xml_path is None:
        candidates = sorted(Path("dump").glob("sawikisource-*.xml"))
        if not candidates:
            print("no dump/*.xml found", file=sys.stderr)
            sys.exit(1)
        xml_path = candidates[0]

    print(f"parsing {xml_path}", file=sys.stderr)
    # namespaces_of_interest=None (the default) resolves Main/Category/Index/
    # Template ids from the dump's own siteinfo -- see DumpIndex in parse_dump.py.
    dump_index = parse_dump(xml_path)

    cat_ns_name = dump_index.namespaces[dump_index.category_ns_id()]

    print("building Main-namespace tree...", file=sys.stderr)
    main_nodes = build_main_tree(dump_index.pages_by_ns[0])

    print("building category graph...", file=sys.stderr)
    graph = build_category_graph(dump_index.pages_by_ns[14], cat_ns_name)
    if graph.root_title not in graph.nodes:
        print(f"error: root category '{graph.root_title}' not found", file=sys.stderr)
        sys.exit(1)

    # धर्मशास्त्रम् is filed on the live site as a top-level sibling of ग्रन्थाः
    # under root -- not useful for readers, since it's really a body of
    # ग्रन्थाः-type texts. Fold it in as a subcategory instead (scrape.py made
    # the same call). See refile_category's docstring for details.
    refile_category(graph, "धर्मशास्त्रम्", new_parent_title="ग्रन्थाः", old_parent_title=graph.root_title)

    print("building transclusion map...", file=sys.stderr)
    transclusion_map = build_transclusion_map(dump_index.pages_by_ns[0])

    print("computing content sizes (this is the slow step)...", file=sys.stderr)
    content_index = compute_all_content_sizes(
        dump_index, transliterate=not args.no_transliterate,
        transclusion_map=transclusion_map, workers=args.workers,
    )

    print("assembling tree...", file=sys.stderr)
    tree = build_tree_json(dump_index, graph, main_nodes, transclusion_map, content_index)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, separators=(",", ":"))

    print(f"wrote {args.out}", file=sys.stderr)
    print(f"root stats: {tree['root']['stats']}", file=sys.stderr)

    dump_date_match = re.search(r"sawikisource-(\d{4}-\d{2}-\d{2})-", xml_path.name)
    dump_date = dump_date_match.group(1) if dump_date_match else ""

    _stamp_data_version(dump_date)

    elapsed = time.time() - run_start
    print(f"total run time: {elapsed:.0f}s ({elapsed / 60:.1f}m)", file=sys.stderr)


def _stamp_data_version(dump_date: str) -> None:
    """Record today's date as __data_version__ (pipeline-run date) and the
    Wikimedia dump export's own date (parsed from the source XML filename,
    e.g. sawikisource-2026-07-01-....xml -> "2026-07-01") as
    __content_version__ in docs/VERSION, alongside __code_version__ (bumped
    manually/separately). __content_version__ is deliberately just the
    dump's snapshot date -- not a rollup over page-edit timestamps, which
    the main panel already surfaces per-item on its own."""
    version_path = Path(__file__).resolve().parent.parent / "docs" / "VERSION"
    today = time.strftime("%Y-%m-%d", time.gmtime())
    lines = version_path.read_text(encoding="utf-8").splitlines() if version_path.exists() else ['__code_version__ = "0.1.0"']
    lines = [
        ln for ln in lines
        if not ln.startswith("__data_version__")
        and not ln.startswith("__content_version__")
    ]
    lines.append(f'__data_version__ = "{today}"')
    if dump_date:
        lines.append(f'__content_version__ = "{dump_date}"')
    version_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
