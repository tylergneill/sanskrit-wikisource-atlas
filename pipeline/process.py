"""
Process stage: runs parse_dump/build_tree/transclusion/content_size in
sequence and assembles their outputs (namespace records, Main subpage tree,
Category digraph, transclusion map, content-size stats) into a single JSON
tree for the frontend.

New schema (deliberately not identical to the old scraper's tree.json --
see notes/sawikisource-scraper-spec.md and the 2026-07 branch decision to
adapt the frontend rather than force new concepts into the old shape):

Node (category):
  { id, type: "category", title, children: [Node], category_pointers: [str],
    pages: [PageNode], index_items: [IndexItemNode], stats }

Node (category-pointer): a second+ filing of a category already emitted
elsewhere in the tree (multi-parent category, see build_tree.CategoryGraph).
  { id, type: "category-pointer", title, points_to: <id>, stats }

PageNode (Main-namespace page, filed into this category via its own direct
[[वर्गः:...]] tag):
  { id, type: "page", title, url, stats, subpages: [PageNode] }
  subpages come from the Main-namespace tree (build_tree.MainPageNode),
  nested the same way the old schema nested them.

IndexItemNode (Index-namespace item with ZERO transclusion anywhere in
Main-namespace content -- i.e. raw/unpublished OCR, per
transclusion.is_transcluded):
  { id, type: "index-item", title, url, stats }
  Never expandable into Page-namespace (scanned-leaf) detail -- consistent
  with the spec's explicit non-goal.

stats: { raw_bytes, content_bytes, transliterated_bytes, count, last_changed }
  count = number of distinct Main pages + Index items reachable from this
  node (dedup'd the same way the old scraper's attach_stats deduped shared
  categories -- see reachable_content() below).
"""

from __future__ import annotations

import argparse
import json
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

ROOT_CATEGORY_TITLE = "वर्गसर्वस्वम्"


def page_url(title: str) -> str:
    from urllib.parse import quote
    return "https://sa.wikisource.org/wiki/" + quote(title.replace(" ", "_"))


def index_url(title: str, index_ns_name: str) -> str:
    from urllib.parse import quote
    return "https://sa.wikisource.org/wiki/" + quote((index_ns_name + ":" + title).replace(" ", "_"))


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


def compute_all_content_sizes(
    dump_index: DumpIndex,
    transliterate: bool,
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

    index_records = dump_index.pages_by_ns[dump_index.index_ns_id()]
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

    return ContentIndex(
        main_sizes=main_sizes,
        main_categories=main_categories,
        index_sizes=index_sizes,
        index_categories=index_categories,
        index_timestamps=index_timestamps,
    )


def _stats_dict(raw: int, content: int, translit: int, count: int, last_changed: str) -> dict:
    return {
        "raw_bytes": raw,
        "content_bytes": content,
        "transliterated_bytes": translit,
        "count": count,
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
    )


def build_page_node(
    main_node: MainPageNode,
    owning_cat_id: str,
    content_index: ContentIndex,
) -> tuple[dict, dict]:
    """Returns (json_node, own_stats) where own_stats covers this page alone
    (not its subpages) -- callers roll subpage stats up separately, same
    own-vs-rollup split the old schema used."""
    size = content_index.main_sizes.get(main_node.title)
    last_changed = main_node.record.timestamp
    own_stats = _stats_dict(
        size.raw_wikitext_bytes if size else 0,
        size.content_bytes if size else 0,
        size.transliterated_bytes if size else 0,
        1,
        last_changed,
    )

    subpage_jsons = []
    rolled = dict(own_stats)
    for child in main_node.children:
        child_json, child_rolled = build_page_node(child, owning_cat_id, content_index)
        subpage_jsons.append(child_json)
        rolled = _merge_stats(rolled, child_rolled)

    node = {
        "id": f"page:{main_node.title}",
        "type": "page",
        "title": main_node.title,
        "url": page_url(main_node.title),
        "stats": rolled,
        "subpages": subpage_jsons,
    }
    return node, rolled


def build_index_item_node(bare_title: str, content_index: ContentIndex, index_ns_name: str) -> dict:
    size = content_index.index_sizes.get(bare_title)
    rec_timestamp = content_index.index_timestamps.get(bare_title, "")
    stats = _stats_dict(
        size.raw_wikitext_bytes if size else 0,
        size.content_bytes if size else 0,
        size.transliterated_bytes if size else 0,
        1,
        rec_timestamp,
    )
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


def build_tree_json(
    dump_index: DumpIndex,
    graph: CategoryGraph,
    main_nodes: dict[str, MainPageNode],
    transclusion_map: dict[str, set[str]],
    content_index: ContentIndex,
) -> dict:
    index_ns_name = dump_index.namespaces[dump_index.index_ns_id()]
    pages_by_cat, index_items_by_cat = build_category_membership_maps(content_index)

    emitted_ids: dict[str, str] = {}  # category title -> id of its first (real) emission
    seen_main_titles_top: set[str] = set()  # titles already emitted as a top-level page under SOME category (dedup across categories)

    def cat_id(title: str) -> str:
        return "cat:" + title

    def build_category(title: str) -> dict:
        node_id = cat_id(title)
        if title in emitted_ids:
            return {
                "id": node_id + ":pointer",
                "type": "category-pointer",
                "title": title,
                "points_to": emitted_ids[title],
                "stats": None,  # filled in by rollup pass
            }
        emitted_ids[title] = node_id

        cat_node = graph.nodes.get(title)
        children = []
        if cat_node is not None:
            for child_title in sorted(cat_node.children):
                children.append(build_category(child_title))

        page_jsons = []
        rolled = _empty_stats()
        for page_title in sorted(pages_by_cat.get(title, [])):
            if page_title in seen_main_titles_top:
                continue  # a page filed under >1 category directly: emitted once, at first category encountered
            main_node = main_nodes.get(page_title)
            if main_node is None or main_node.parent_title is not None:
                continue  # not a Main record, or it's itself a subpage of another page (nested there instead)
            seen_main_titles_top.add(page_title)
            page_json, page_rolled = build_page_node(main_node, node_id, content_index)
            page_jsons.append(page_json)
            rolled = _merge_stats(rolled, page_rolled)

        index_jsons = []
        for bare_title in sorted(index_items_by_cat.get(title, [])):
            if is_transcluded(bare_title, transclusion_map):
                continue  # published elsewhere in Main -- drop the raw Index item per spec
            index_json = build_index_item_node(bare_title, content_index, index_ns_name)
            index_jsons.append(index_json)
            rolled = _merge_stats(rolled, index_json["stats"])

        for child in children:
            if child["type"] == "category-pointer":
                continue  # pointer stats are resolved in the rollup pass, not summed here (would double count)
            rolled = _merge_stats(rolled, child["stats"])

        return {
            "id": node_id,
            "type": "category",
            "title": title,
            "children": children,
            "pages": page_jsons,
            "index_items": index_jsons,
            "stats": rolled,
        }

    root = build_category(graph.root_title)

    # Resolve category-pointer stats (they point to a node built earlier in
    # the same walk, whose stats dict now exists in emitted_ids' owner).
    by_id: dict[str, dict] = {}

    def index_by_id(node: dict) -> None:
        by_id[node["id"]] = node
        for ch in node.get("children", []):
            index_by_id(ch)

    index_by_id(root)

    def resolve_pointers(node: dict) -> None:
        for i, child in enumerate(node.get("children", [])):
            if child["type"] == "category-pointer" and child["stats"] is None:
                target = by_id.get(child["points_to"])
                child["stats"] = target["stats"] if target else _empty_stats()
            resolve_pointers(child)

    resolve_pointers(root)

    # वर्गसर्वस्वम् (the literal MediaWiki category root) isn't a useful node to
    # show readers -- once the junk siblings are excluded (see
    # EXCLUDED_CATEGORIES) and धर्मशास्त्रम् is folded into ग्रन्थाः (see
    # refile_category in main()), root has exactly one real child, ग्रन्थाः,
    # which just adds an extra meaningless click. Splice ग्रन्थाः's own
    # contents up to root directly, same as scrape.py did (it crawled
    # starting *at* ग्रन्थाः and never emitted it as a node at all).
    granth = next((c for c in root["children"] if c["title"] == "ग्रन्थाः" and c["type"] == "category"), None)
    if granth is not None:
        root = {
            "id": "root",
            "type": "category",
            "title": "ग्रन्थाः (धर्मशास्त्राणि च)",
            "children": granth["children"],
            "pages": granth["pages"],
            "index_items": granth["index_items"],
            "stats": granth["stats"],
        }

    return {"root": root}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml_path", type=Path, nargs="?", help="path to the uncompressed dump XML")
    parser.add_argument("--out", type=Path, default=Path("docs2/data/tree2.json"),
                         help="output path (default: docs2/data/tree2.json, the v2 frontend's data dir)")
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
        dump_index, transliterate=not args.no_transliterate, workers=args.workers
    )

    print("assembling tree...", file=sys.stderr)
    tree = build_tree_json(dump_index, graph, main_nodes, transclusion_map, content_index)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, separators=(",", ":"))

    print(f"wrote {args.out}", file=sys.stderr)
    print(f"root stats: {tree['root']['stats']}", file=sys.stderr)

    elapsed = time.time() - run_start
    print(f"total run time: {elapsed:.0f}s ({elapsed / 60:.1f}m)", file=sys.stderr)


if __name__ == "__main__":
    main()
