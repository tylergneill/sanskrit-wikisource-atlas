"""
Build stage, part 2: construct the Main-namespace subpage tree and the
Category digraph from parsed dump records (pipeline.parse_dump.DumpIndex).

Per notes/sawikisource-scraper-spec.md:
- Main-namespace parent/child is a genuine tree, derived purely by splitting
  titles on the last "/" -- no multi-parent, no cycles possible by
  construction, so rollups need no dedup logic.
- The Category graph is a manually-maintained, directed, NOT-guaranteed-
  acyclic graph (edges from [[वर्गः:Parent]] tags on each category's own
  body). Multi-parenting is real (a category can have >1 parent) and not
  everything reaches root (वर्गसर्वस्वम्) -- disconnected components exist.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.parse_dump import DumpIndex, PageRecord, category_links, is_excluded_category, parse_dump

ROOT_CATEGORY_TITLE = "वर्गसर्वस्वम्"


# ---------------------------------------------------------------------------
# Main-namespace subpage tree
# ---------------------------------------------------------------------------

@dataclass
class MainPageNode:
    record: PageRecord
    title: str  # full title, e.g. "Work/Part 1"
    parent_title: str | None  # None for a top-level (no "/") title
    children: list["MainPageNode"] = field(default_factory=list)


def build_main_tree(records: list[PageRecord]) -> dict[str, MainPageNode]:
    """Returns a title -> MainPageNode map covering every Main-namespace page
    (redirects included, as leaf nodes -- callers filter/dereference as needed).
    Parent/child edges are purely a title-string convention: a node's parent
    is "everything before the last '/'" in its own title, IF that exact title
    exists as another page in this same namespace -- a "/" with no matching
    parent title present (e.g. an orphaned or malformed subpage title) is
    left as its own top-level node, not synthesized into a parent that
    doesn't exist as real content.
    """
    nodes: dict[str, MainPageNode] = {}
    for rec in records:
        parent_title = None
        if "/" in rec.title:
            parent_title = rec.title.rsplit("/", 1)[0]
        nodes[rec.title] = MainPageNode(record=rec, title=rec.title, parent_title=parent_title)

    for node in nodes.values():
        if node.parent_title is not None and node.parent_title in nodes:
            nodes[node.parent_title].children.append(node)
        else:
            node.parent_title = None  # no real parent page -- treat as top-level

    return nodes


def main_tree_roots(nodes: dict[str, MainPageNode]) -> list[MainPageNode]:
    return [n for n in nodes.values() if n.parent_title is None]


# ---------------------------------------------------------------------------
# Category digraph
# ---------------------------------------------------------------------------

@dataclass
class CategoryNode:
    title: str  # bare title, no "वर्गः:" prefix
    record: PageRecord | None  # None if referenced (e.g. as a parent) but never itself a page in the dump
    parents: set[str] = field(default_factory=set)
    children: set[str] = field(default_factory=set)


@dataclass
class CategoryGraph:
    nodes: dict[str, CategoryNode]
    root_title: str

    def reachable_descendants(self, title: str, _memo: dict[str, frozenset] | None = None,
                               _visiting: set[str] | None = None) -> frozenset[str]:
        """Memoized set of all category titles reachable downward from `title`
        (title itself included). Cycle-safe: a category currently being
        visited on the current path is treated as contributing no further
        descendants when re-encountered, breaking the cycle rather than
        recursing forever.
        """
        if _memo is None:
            _memo = {}
        if _visiting is None:
            _visiting = set()
        if title in _memo:
            return _memo[title]
        if title in _visiting:
            return frozenset()  # cycle guard
        _visiting.add(title)

        result = {title}
        node = self.nodes.get(title)
        if node is not None:
            for child in node.children:
                result |= self.reachable_descendants(child, _memo, _visiting)

        _visiting.discard(title)
        frozen = frozenset(result)
        _memo[title] = frozen
        return frozen


def build_category_graph(records: list[PageRecord], category_ns_name: str) -> CategoryGraph:
    """Builds the category digraph from Category-namespace page bodies.
    Nodes are created both for every real Category-namespace page AND for
    any category named as a parent that has no page of its own (a common
    real case: [[वर्गः:SomeParent]] where SomeParent was never separately
    created) -- represented with record=None so it's still a valid graph
    node, just with no stats/content of its own.
    Excluded (maintenance/junk) categories per is_excluded_category are
    dropped entirely, from both nodes and any edges naming them.
    """
    nodes: dict[str, CategoryNode] = {}

    def get_or_create(title: str) -> CategoryNode:
        if title not in nodes:
            nodes[title] = CategoryNode(title=title, record=None)
        return nodes[title]

    for rec in records:
        title = rec.title.split(":", 1)[1].strip() if ":" in rec.title else rec.title.strip()
        if is_excluded_category(title):
            continue
        node = get_or_create(title)
        node.record = rec

        for parent_title in category_links(rec.text, category_ns_name):
            if is_excluded_category(parent_title):
                continue
            parent_node = get_or_create(parent_title)
            parent_node.children.add(title)
            node.parents.add(parent_title)

    return CategoryGraph(nodes=nodes, root_title=ROOT_CATEGORY_TITLE)


def orphaned_category_titles(graph: CategoryGraph) -> list[str]:
    """Categories with zero parents that are also not the root itself --
    per the spec, these are real (disconnected components), not an error,
    and should be listed separately rather than forced under root."""
    return sorted(
        title for title, node in graph.nodes.items()
        if not node.parents and title != graph.root_title
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml_path", type=Path, nargs="?", help="path to the uncompressed dump XML")
    args = parser.parse_args()

    xml_path = args.xml_path
    if xml_path is None:
        candidates = sorted(p for p in Path("dump").glob("sawikisource-*.xml"))
        if not candidates:
            print("no dump/*.xml found", file=sys.stderr)
            sys.exit(1)
        xml_path = candidates[0]

    dump_index: DumpIndex = parse_dump(xml_path)
    cat_ns_name = dump_index.namespaces[dump_index.category_ns_id()]

    main_nodes = build_main_tree(dump_index.pages_by_ns[0])
    roots = main_tree_roots(main_nodes)
    print(f"main tree: {len(main_nodes)} pages, {len(roots)} top-level (no-parent) titles", file=sys.stderr)

    graph = build_category_graph(dump_index.pages_by_ns[14], cat_ns_name)
    print(f"category graph: {len(graph.nodes)} nodes", file=sys.stderr)

    if graph.root_title not in graph.nodes:
        print(f"warning: root category '{graph.root_title}' not found in graph", file=sys.stderr)
    else:
        descendants = graph.reachable_descendants(graph.root_title)
        print(f"reachable from root '{graph.root_title}': {len(descendants)} categories", file=sys.stderr)

    orphans = orphaned_category_titles(graph)
    print(f"orphaned (no-parent, non-root) categories: {len(orphans)}", file=sys.stderr)


if __name__ == "__main__":
    main()
