#!/usr/bin/env python3
"""Gross corpus stats for pre-2012 sa.wikisource snapshots.

See pre-2012-corpus-history.md in this directory for the findings.

One-off historical analysis, not part of the maintained pipeline.

The real pipeline can't process these months at all -- process_dump raises
RootCategoryMissing, because वर्गसर्वस्वम् (created 2012-01-20) doesn't exist
yet and the whole tree model descends from it. But the three headline numbers
(MB, pages, texts) never actually needed the category graph: they come from
the Main-namespace subpage tree, the transclusion map, and content sizes --
all category-free. So this runs exactly the same pipeline stages as
backfill.process_dump, minus build_category_graph/build_tree_json, and sums
the same per-node stats build_page_node/build_index_item_node would have.

Definitions mirrored from pipeline/process.py:
  texts  = Main pages with parent_title is None and no redirect_target
           (process.py:398 + :612-614), plus untranscluded Index items
           (process.py:456, always text_count=1)
  pages  = distinct non-redirect Main pages + untranscluded Index items
           (every subpage counted individually, per CLAUDE.md's `count`)
  bytes  = content_index raw/content/transliterated, with each untranscluded
           Index item's पृष्ठम्:Title/N leaf rollup folded in the same way
           build_index_item_node does.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # notes/pre-2012/ -> repo root
sys.path.insert(0, str(REPO))

from pipeline.parse_dump import parse_dump
from pipeline.build_tree import build_main_tree
from pipeline.transclusion import build_transclusion_map, is_transcluded
from pipeline.process import compute_all_content_sizes


def stats_for(xml_path: Path) -> dict:
    dump_index = parse_dump(xml_path)

    main_records = dump_index.pages_by_ns[0]
    main_nodes = build_main_tree(main_records)
    transclusion_map = build_transclusion_map(main_records)

    content_index = compute_all_content_sizes(
        dump_index, transliterate=True, transclusion_map=transclusion_map,
    )

    # --- Main namespace: skip redirect stubs exactly as process.py does ---
    raw = content = translit = 0
    page_count = 0
    text_count = 0
    for title, node in main_nodes.items():
        if node.record.redirect_target is not None:
            continue
        page_count += 1
        if node.parent_title is None:
            text_count += 1
        size = content_index.main_sizes.get(title)
        if size:
            raw += size.raw_wikitext_bytes
            content += size.content_bytes
            translit += size.transliterated_bytes

    # --- Index namespace: only untranscluded items are their own nodes ---
    index_items = 0
    for bare_title in content_index.index_categories:
        if is_transcluded(bare_title, transclusion_map):
            continue
        index_items += 1
        page_count += 1
        text_count += 1
        size = content_index.index_sizes.get(bare_title)
        if size:
            raw += size.raw_wikitext_bytes
            content += size.content_bytes
            translit += size.transliterated_bytes
        # index_page_rollup values are plain stats dicts (see process.py's
        # _stats_dict), not ContentSizeResult objects like *_sizes above.
        rollup = content_index.index_page_rollup.get(bare_title)
        if rollup:
            raw += rollup["raw_bytes"]
            content += rollup["content_bytes"]
            translit += rollup["transliterated_bytes"]

    # Categories that exist at all, for context on when the system appeared
    cat_ns_id = dump_index.category_ns_id()
    n_categories = len(dump_index.pages_by_ns.get(cat_ns_id, []))
    has_index_ns = dump_index.index_ns_id() is not None

    return {
        "raw_bytes": raw,
        "content_bytes": content,
        "transliterated_bytes": translit,
        "pages": page_count,
        "texts": text_count,
        "untranscluded_index_items": index_items,
        "categories_on_site": n_categories,
        "proofreadpage_enabled": has_index_ns,
        "main_records_incl_redirects": len(main_records),
    }


def main():
    snap_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    results = {}
    for xml in sorted(snap_dir.glob("*.xml")):
        # sawikisource-20040723-pages-articles.synth.xml -> 2004-07-23
        day8 = xml.name.split("-")[1]
        date = f"{day8[:4]}-{day8[4:6]}-{day8[6:]}"
        print(f"\n===== {date} ({xml.name}) =====", file=sys.stderr)
        results[date] = stats_for(xml)
        print(json.dumps(results[date], ensure_ascii=False), file=sys.stderr)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
