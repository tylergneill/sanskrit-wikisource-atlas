"""
Audit stage: surfaces likely breadcrumb and category structural problems on
the live wiki for a human to review and fix directly on sa.wikisource.org --
this tool never mutates wiki content, the dump, or docs/data/tree.json, and
never applies any inference/correction of its own. See
notes/wikisource-editing-plan.md for the editing campaign this feeds.

Six independent checks, run over a single dump:

1. Breadcrumb-gap candidates: a top-level Main page T ("तन्त्रालोकः") that
   has other top-level pages sharing its title as a space-separated prefix
   ("तन्त्रालोकः अष्टममाह्निकम्") instead of a real "/"-delimited MediaWiki
   subpage title ("तन्त्रालोकः/अष्टममाह्निकम्"). Requiring the prefix ITSELF
   to be a real page title (not just any shared leading words) keeps the
   false-positive rate low -- this is meant to catch "should have used
   subpage syntax but didn't", not coincidental title overlap. Candidates
   are grouped and counted per work, never auto-renamed.

2. Root category-inference candidates: pipeline.transclusion.infer_root_categories,
   wired up here as a detector (not silent inference -- see
   notes/wikisource-editing-plan.md, "A related, adjacent finding: dead
   category-inference code"). Flags a top-level page whose real "/" subpages
   ALL share a category tag the top-level page itself lacks, for a human to
   add the tag upstream on the wiki.

3. Orphaned categories: a real Category-namespace page with zero parent tags
   of its own (other than the root itself) -- see
   pipeline.build_tree.orphaned_category_titles. The category-graph-level
   sibling of the Main-namespace orphan bucket (असम्बद्धवर्गीकृतम्): the
   category page exists, it's just never filed under anything, so nothing
   tagged into it is reachable from वर्गसर्वस्वम् either.

4. Red-link categories: a category named as a parent or child via
   [[वर्गः:X]] somewhere, where X was never itself created as a real
   Category-namespace page (pipeline.build_tree.CategoryGraph node with
   record=None). Not fixable by re-tagging -- the category page itself needs
   to be created on-wiki.

5. Category cycles: the About page (docs/about.html, "Categories: Another
   Graph") explicitly warns categorization is manual and "there can even be
   cycles" -- a category graph is supposed to be a tree, so a directed cycle
   here is always a mis-tagging. Detected via standard DFS
   white/gray/black coloring over CategoryGraph.

6. Page-namespace (पृष्ठम्) items carrying their own direct category tag --
   against the site's stated convention (docs/about.html, "OCR
   'Proofreading' Pipeline Types": "Index items can be labeled with
   Categories, but Pages should not be", since Pages are numerous enough per
   book that showing them all under a Category would be unusable).

7. External bulk-import candidates: a Main-namespace page whose external
   links are dominated by (or which carries an explicit source-attribution
   header pointing to) a known digitized-Sanskrit-text repository (GRETIL,
   sanskritdocuments.org, muktalib7.com, SARIT, etc.) -- i.e. likely
   copy-pasted wholesale from elsewhere rather than transcribed/composed on
   sa.wikisource itself. Not a defect to fix in the usual sense (attribution
   is good practice, not a bug), but worth surfacing since it's part of
   understanding how much of the corpus is original-to-the-wiki vs. an
   unreviewed import -- see notes/wikisource-editing-plan.md. Deliberately
   excludes domains found to be inline citation/cross-reference targets
   scattered across many otherwise-original pages (e.g. avg-sanskrit.org
   Panini-sutra links, or the puranastudy.*/vedastudy.* footnote-mirror
   farm) -- those aren't "this whole page came from there" signals.

Usage:
    python -m pipeline.audit [xml_path]
    (defaults to the newest dump/sawikisource-*.xml, same convention as
    pipeline.process)
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from pipeline.build_tree import (
    CategoryGraph,
    MainPageNode,
    build_category_graph,
    build_main_tree,
    orphaned_category_titles,
)
from pipeline.parse_dump import PageRecord, is_excluded_category, parse_dump
from pipeline.progress import to_iast
from pipeline.transclusion import direct_categories, infer_root_categories

# Domains confirmed (by manual spot-check against the 2026-07-01 dump) to host
# machine-readable digitized Sanskrit text corpora, as opposed to sites that
# merely get cited inline (avg-sanskrit.org's per-sutra cross-references) or
# rehosted across many free-hosting mirrors as footnote targets (the
# puranastudy.*/vedastudy.* cluster) -- neither of those is a "whole page
# copy-pasted from here" signal, so they're deliberately excluded.
BULK_TEXT_REPO_DOMAINS = {
    "gretil.sub.uni-goettingen.de", "sub.uni-goettingen.de", "detu.sub.uni-goettingen.de",
    "sanskritdocuments.org", "sarit.indology.info",
    "kjc-fs-cluster.kjc.uni-heidelberg.de",
    "muktalib7.com", "tipitakapali.org", "glossaries.dila.edu.tw",
    "titus.uni-frankfurt.de",
}

_URL_RE = re.compile(r"https?://[^\s\]\|\}\)]+")
_SOURCE_HEADER_RE = re.compile(r"==\s*(स्रोतः|मूलपाठः|आधारः)\s*==")


def find_breadcrumb_gap_candidates(
    main_nodes: dict[str, MainPageNode],
) -> dict[str, list[str]]:
    """Returns {top-level title: [other top-level titles sharing it as a
    space-separated prefix]}, for top-level titles that have at least one
    such candidate. Redirects are excluded on both sides -- a redirect
    sharing a prefix is noise (an alternate name/typo target), not a
    mis-titled chapter."""
    top_level_titles = sorted(
        title for title, node in main_nodes.items()
        if node.parent_title is None and node.record.redirect_target is None
    )

    candidates: dict[str, list[str]] = defaultdict(list)
    for prefix in top_level_titles:
        prefix_space = prefix + " "
        for other in top_level_titles:
            if other != prefix and other.startswith(prefix_space):
                candidates[prefix].append(other)

    return {k: v for k, v in candidates.items() if v}


def find_root_inference_candidates(
    main_nodes: dict[str, MainPageNode],
    direct_cats_by_title: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Returns {top-level title with real "/" subpages: set of categories
    every one of its subpages shares that the top-level page itself lacks}."""
    candidates: dict[str, set[str]] = {}
    for title, node in main_nodes.items():
        if node.parent_title is not None or not node.children:
            continue
        subpage_titles = [child.title for child in node.children]
        inferred = infer_root_categories(title, subpage_titles, direct_cats_by_title)
        if inferred:
            candidates[title] = inferred
    return candidates


def find_orphaned_categories(graph: CategoryGraph) -> list[str]:
    """Real Category-namespace pages with zero parent tags of their own
    (excluding the root) -- thin wrapper over
    pipeline.build_tree.orphaned_category_titles, restricted to categories
    that actually have their own page (red-link categories are reported
    separately, see find_redlink_categories, since the fix is different:
    create the page vs. add a parent tag)."""
    return [
        t for t in orphaned_category_titles(graph)
        if graph.nodes[t].record is not None
    ]


def find_redlink_categories(graph: CategoryGraph) -> list[str]:
    """Categories named as a parent or child via [[वर्गः:X]] somewhere, where
    X itself was never created as a real Category-namespace page."""
    return sorted(t for t, n in graph.nodes.items() if n.record is None)


def find_category_cycles(graph: CategoryGraph) -> list[list[str]]:
    """Standard DFS cycle detection (white/gray/black coloring) over the
    category digraph's parent -> child edges. Returns one path per detected
    back-edge, from the cycle's start back to itself -- a category graph is
    supposed to be a tree (see docs/about.html), so any cycle here is always
    a mis-tagging, not a modeling choice."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {t: WHITE for t in graph.nodes}
    cycles: list[list[str]] = []

    def dfs(title: str, path: list[str]) -> None:
        color[title] = GRAY
        path.append(title)
        node = graph.nodes.get(title)
        if node is not None:
            for child in sorted(node.children):
                if color.get(child, WHITE) == WHITE:
                    dfs(child, path)
                elif color.get(child) == GRAY:
                    idx = path.index(child)
                    cycles.append(path[idx:] + [child])
        path.pop()
        color[title] = BLACK

    for title in sorted(graph.nodes):
        if color[title] == WHITE:
            dfs(title, [])

    return cycles


def find_tagged_page_ns_items(
    page_records: list,
    cat_ns_name: str,
) -> dict[str, set[str]]:
    """Page-namespace (पृष्ठम्) records carrying their own direct category
    tag -- against the site's convention that only Index items, not
    individual scanned Pages, should be categorized."""
    return {
        rec.title: cats
        for rec in page_records
        if (cats := direct_categories(rec, cat_ns_name))
    }


def find_bulk_import_candidates(
    main_records: list[PageRecord],
) -> dict[str, set[str]]:
    """Returns {page title: {repo domains}} for Main-namespace pages whose
    external links are dominated by a known bulk-text-repo domain (nearly
    all of the page's external links go to it) or which carry an explicit
    source-attribution section header -- either signal on its own indicates
    the page content likely originates wholesale from that repository rather
    than from transcription/composition on sa.wikisource. A repo domain
    appearing only as one citation among many unrelated external links
    (footnote/cross-reference style) does not qualify."""
    candidates: dict[str, set[str]] = {}
    for rec in main_records:
        urls = _URL_RE.findall(rec.text)
        if not urls:
            continue
        domains = [
            re.sub(r"^www\.", "", urlparse(u.rstrip("].,")).netloc.lower())
            for u in urls
        ]
        repo_domains = {d for d in domains if d in BULK_TEXT_REPO_DOMAINS}
        if not repo_domains:
            continue
        repo_hits = sum(1 for d in domains if d in repo_domains)
        dominant = repo_hits >= max(1, len(domains) - 1)
        if dominant or _SOURCE_HEADER_RE.search(rec.text):
            candidates[rec.title] = repo_domains
    return candidates


def print_report(
    breadcrumb_candidates: dict[str, list[str]],
    inference_candidates: dict[str, set[str]],
    orphaned_categories: list[str],
    redlink_categories: list[str],
    category_cycles: list[list[str]],
    tagged_page_ns_items: dict[str, set[str]],
    bulk_import_candidates: dict[str, set[str]],
) -> None:
    """All Devanagari data values (titles, category names) are transliterated
    to IAST before printing -- terminal-output-only, same convention as
    pipeline.progress.to_iast's docstring: never touches persisted data, only
    what's shown while a human is reading this report."""
    total_pages = sum(len(v) for v in breadcrumb_candidates.values())
    print("=" * 70)
    print(f"BREADCRUMB-GAP CANDIDATES: {len(breadcrumb_candidates)} works, "
          f"{total_pages} pages implicated")
    print("=" * 70)
    print("(top-level page titles with other top-level pages sharing them as")
    print(" a space-separated prefix -- candidates for '/' subpage syntax.")
    print(" Review each on-wiki before moving; this tool does not rename pages.)")
    print()
    for title in sorted(breadcrumb_candidates, key=lambda t: -len(breadcrumb_candidates[t])):
        others = breadcrumb_candidates[title]
        print(f"  {to_iast(title)}  ({len(others)} candidate pages)")
        for other in others[:5]:
            print(f"      {to_iast(other)}")
        if len(others) > 5:
            print(f"      ... and {len(others) - 5} more")
    print()

    print("=" * 70)
    print(f"ROOT CATEGORY-INFERENCE CANDIDATES: {len(inference_candidates)} works")
    print("=" * 70)
    print("(top-level pages whose real '/' subpages ALL share a category tag")
    print(" the top-level page itself lacks -- candidate for adding that tag")
    print(" directly to the top-level page on-wiki. This tool does not infer")
    print(" or apply the tag.)")
    print()
    for title in sorted(inference_candidates):
        cats = inference_candidates[title]
        print(f"  {to_iast(title)}  -> missing: {', '.join(to_iast(c) for c in sorted(cats))}")
    print()

    print("=" * 70)
    print(f"ORPHANED CATEGORIES: {len(orphaned_categories)}")
    print("=" * 70)
    print("(real Category pages with no parent tag of their own -- add a")
    print(" [[category:Parent]] tag on-wiki to file them under the real tree.)")
    print()
    for title in sorted(orphaned_categories):
        print(f"  {to_iast(title)}")
    print()

    print("=" * 70)
    print(f"RED-LINK CATEGORIES: {len(redlink_categories)}")
    print("=" * 70)
    print("(named as a parent/child via [[category:X]] somewhere, but X was never")
    print(" created as its own Category page on-wiki.)")
    print()
    for title in sorted(redlink_categories):
        print(f"  {to_iast(title)}")
    print()

    print("=" * 70)
    print(f"CATEGORY CYCLES: {len(category_cycles)}")
    print("=" * 70)
    print("(a category graph should be a tree -- any cycle here is a")
    print(" mis-tagging on-wiki, not a modeling choice.)")
    print()
    for cycle in category_cycles:
        print(f"  {' -> '.join(to_iast(t) for t in cycle)}")
    print()

    print("=" * 70)
    print(f"CATEGORY-TAGGED PAGE-NAMESPACE ITEMS: {len(tagged_page_ns_items)}")
    print("=" * 70)
    print("(Page: scanned-leaf items should not carry their own Category tag")
    print(" by site convention -- move the tag to the owning Index item instead.)")
    print()
    for title in sorted(tagged_page_ns_items):
        cats = tagged_page_ns_items[title]
        print(f"  {to_iast(title)}  -> {', '.join(to_iast(c) for c in sorted(cats))}")
    print()

    print("=" * 70)
    print(f"EXTERNAL BULK-IMPORT CANDIDATES: {len(bulk_import_candidates)}")
    print("=" * 70)
    print("(page content likely copy-pasted wholesale from a known digitized-text")
    print(" repository -- not a defect, but worth knowing for corpus provenance.")
    print(" Not renamed/tagged/altered by this tool.)")
    print()
    for title in sorted(bulk_import_candidates):
        domains = bulk_import_candidates[title]
        print(f"  {to_iast(title)}  -> {', '.join(sorted(domains))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xml_path", type=Path, nargs="?", help="path to the uncompressed dump XML")
    args = parser.parse_args()

    xml_path = args.xml_path
    if xml_path is None:
        candidates = sorted(Path("dump").glob("sawikisource-*.xml"))
        if not candidates:
            print("no dump/*.xml found", file=sys.stderr)
            sys.exit(1)
        xml_path = candidates[0]

    print(f"parsing {xml_path}", file=sys.stderr)
    # Page (104) is the ProofreadPage extension -- absent from early dumps,
    # same caveat as elsewhere in this pipeline (see parse_dump.DumpIndex).
    # namespaces_of_interest is resolved AFTER siteinfo is read, so page_ns_id()
    # can't be called until parse_dump has already run once; instead pass None
    # (default: every namespace this pipeline ever cares about) since Main +
    # Category + Page is most of the dump anyway and Index/Template add little
    # extra cost here without content-size computation.
    dump_index = parse_dump(xml_path, namespaces_of_interest=None)

    cat_ns_name = dump_index.namespaces[dump_index.category_ns_id()]
    main_records = dump_index.pages_by_ns[0]
    page_ns_id = dump_index.page_ns_id()
    page_records = dump_index.pages_by_ns.get(page_ns_id, []) if page_ns_id is not None else []

    main_nodes = build_main_tree(main_records)

    print("building category graph...", file=sys.stderr)
    graph = build_category_graph(dump_index.pages_by_ns[14], cat_ns_name)

    direct_cats_by_title = {
        rec.title: {c for c in direct_categories(rec, cat_ns_name) if not is_excluded_category(c)}
        for rec in main_records
    }

    breadcrumb_candidates = find_breadcrumb_gap_candidates(main_nodes)
    inference_candidates = find_root_inference_candidates(main_nodes, direct_cats_by_title)
    orphaned_categories = find_orphaned_categories(graph)
    redlink_categories = find_redlink_categories(graph)
    category_cycles = find_category_cycles(graph)
    tagged_page_ns_items = find_tagged_page_ns_items(page_records, cat_ns_name)
    bulk_import_candidates = find_bulk_import_candidates(main_records)

    print_report(
        breadcrumb_candidates, inference_candidates,
        orphaned_categories, redlink_categories, category_cycles, tagged_page_ns_items,
        bulk_import_candidates,
    )


if __name__ == "__main__":
    main()
