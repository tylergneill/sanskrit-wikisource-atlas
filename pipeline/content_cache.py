"""Lightweight per-month cache of build_tree_json's inputs, so a
build_tree_json/build_category_graph/build_main_tree-level logic fix can be
propagated into docs/data/changelog.json by re-running just the cheap
assembly step, without re-parsing a (long since deleted) raw dump XML or
re-running compute_all_content_sizes (the slow step -- mwparserfromhell
parsing, template expansion, skrutable transliteration). See
notes/richer-backfill-snapshots-plan.md for the full rationale.

Deliberately excludes ContentSizeResult's stripped_text/transliterated_text
(large, and build_tree_json never reads them -- only the three byte counts).
Category-namespace page bodies are cached verbatim (title -> wikitext) rather
than pre-built graph edges, so a FUTURE build_category_graph-level logic
change (not just build_tree_json-level) can also be replayed from this same
cache -- there are only ~250 categories, so this costs almost nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline.build_tree import CategoryGraph, MainPageNode, build_category_graph, build_main_tree, refile_category
from pipeline.content_size import ContentSizeResult
from pipeline.parse_dump import DumpIndex, PageRecord
from pipeline.process import ContentIndex
from pipeline.snapshot_io import read_json_gz, write_json_gz
from pipeline.transclusion import build_reverse_transclusion_map, build_transclusion_map, transcluded_index_titles

CACHE_SCHEMA_VERSION = 1


def _size_to_ints(size: ContentSizeResult | None) -> dict[str, int]:
    if size is None:
        return {"raw": 0, "content": 0, "translit": 0}
    return {"raw": size.raw_wikitext_bytes, "content": size.content_bytes, "translit": size.transliterated_bytes}


def _stats_to_ints(stats: dict) -> dict[str, int]:
    return {
        "raw": stats.get("raw_bytes", 0) or 0,
        "content": stats.get("content_bytes", 0) or 0,
        "translit": stats.get("transliterated_bytes", 0) or 0,
    }


def build_content_cache(
    dump_index: DumpIndex,
    content_index: ContentIndex,
    main_records: list[PageRecord],
    category_records: list[PageRecord],
) -> dict:
    """Assembles the cache dict written to content-<date>.json.gz. Called
    right after compute_all_content_sizes (process_dump/backfill's
    process_dump), while all the inputs are still in memory."""
    cat_ns_id = dump_index.category_ns_id()
    index_ns_id = dump_index.index_ns_id()

    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "namespaces": {str(k): v for k, v in dump_index.namespaces.items()},
        "category_ns_id": cat_ns_id,
        "index_ns_id": index_ns_id,
        "main_sizes": {t: _size_to_ints(s) for t, s in content_index.main_sizes.items()},
        "index_sizes": {t: _size_to_ints(s) for t, s in content_index.index_sizes.items()},
        "main_categories": {t: sorted(cats) for t, cats in content_index.main_categories.items()},
        "index_categories": {t: sorted(cats) for t, cats in content_index.index_categories.items()},
        "index_timestamps": dict(content_index.index_timestamps),
        "index_page_rollup": {t: _stats_to_ints(s) for t, s in content_index.index_page_rollup.items()},
        # Per Main-namespace page: enough to rebuild MainPageNode's tree
        # (redirect_target, for _resolve_redirect) and build_page_node's
        # last_changed, without re-parsing the dump.
        "main_pages": {
            rec.title: {
                "redirect_target": rec.redirect_target,
                "timestamp": rec.timestamp,
                # Cheap to derive (regex over rec.text, see
                # transclusion.transcluded_index_titles) but rec.text itself
                # isn't cached -- store the small derived result instead.
                "transcludes": sorted(transcluded_index_titles(rec.text)),
            }
            for rec in main_records
        },
        # Category-namespace page bodies, verbatim -- see module docstring.
        "category_pages": {
            rec.title: rec.text for rec in category_records
        },
    }


def write_content_cache(path: Path, cache: dict) -> None:
    write_json_gz(path, cache)


@dataclass
class RebuildInputs:
    """Everything build_tree_json needs, reconstructed from a content cache
    without touching the original (deleted) raw dump XML."""
    dump_index: DumpIndex
    graph: CategoryGraph
    main_nodes: dict[str, MainPageNode]
    transclusion_map: dict[str, set[str]]
    reverse_transclusion_map: dict[str, set[str]]
    content_index: ContentIndex


def load_content_cache(path: Path) -> dict:
    cache = read_json_gz(path)
    if cache.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: content cache schema_version {cache.get('schema_version')!r} != "
            f"current {CACHE_SCHEMA_VERSION!r} -- discard and rebuild from a full dump instead"
        )
    return cache


def rebuild_inputs_from_cache(cache: dict) -> RebuildInputs:
    """Reverses build_content_cache: reconstructs the small in-memory shapes
    build_tree_json consumes (ContentIndex, CategoryGraph, main_nodes,
    transclusion maps) purely from cached data -- no dump XML, no
    compute_all_content_sizes re-run."""
    namespaces = {int(k): v for k, v in cache["namespaces"].items()}
    dump_index = DumpIndex(site_name="", namespaces=namespaces, pages_by_ns={})

    def size_result(ints: dict) -> ContentSizeResult:
        return ContentSizeResult(
            raw_wikitext_bytes=ints["raw"],
            stripped_text="",
            content_bytes=ints["content"],
            transliterated_text="",
            transliterated_bytes=ints["translit"],
        )

    def stats_dict(ints: dict) -> dict:
        return {
            "raw_bytes": ints["raw"],
            "content_bytes": ints["content"],
            "transliterated_bytes": ints["translit"],
            "count": 0,
            "text_count": 0,
            "last_changed": "",
        }

    content_index = ContentIndex(
        main_sizes={t: size_result(v) for t, v in cache["main_sizes"].items()},
        index_sizes={t: size_result(v) for t, v in cache["index_sizes"].items()},
        main_categories={t: set(v) for t, v in cache["main_categories"].items()},
        index_categories={t: set(v) for t, v in cache["index_categories"].items()},
        index_timestamps=dict(cache["index_timestamps"]),
        index_page_rollup={t: stats_dict(v) for t, v in cache["index_page_rollup"].items()},
    )

    cat_ns_name = namespaces[cache["category_ns_id"]]
    category_records = [
        PageRecord(pageid=0, ns=cache["category_ns_id"], title=title, redirect_target=None,
                   text=text, timestamp="", revid=0)
        for title, text in cache["category_pages"].items()
    ]
    graph = build_category_graph(category_records, cat_ns_name)

    main_records = [
        PageRecord(pageid=0, ns=0, title=title, redirect_target=fields["redirect_target"],
                   text="", timestamp=fields["timestamp"], revid=0)
        for title, fields in cache["main_pages"].items()
    ]
    main_nodes = build_main_tree(main_records)

    transclusion_map: dict[str, set[str]] = {}
    reverse_transclusion_map: dict[str, set[str]] = {}
    for title, fields in cache["main_pages"].items():
        titles = set(fields.get("transcludes") or [])
        if titles:
            reverse_transclusion_map[title] = titles
            for index_title in titles:
                transclusion_map.setdefault(index_title, set()).add(title)

    return RebuildInputs(
        dump_index=dump_index,
        graph=graph,
        main_nodes=main_nodes,
        transclusion_map=transclusion_map,
        reverse_transclusion_map=reverse_transclusion_map,
        content_index=content_index,
    )
