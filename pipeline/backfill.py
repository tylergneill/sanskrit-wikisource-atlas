"""Backfill historical docs2 changelog entries from older monthly dump exports.

As of this writing, only 3 months of mediawiki_content_current exports are
available online for sawikisource -- 2026-05-01, 2026-06-01, 2026-07-01.
The format itself launched 2026-01-30 (announced on xmldatadumps-l), and the
same 3-month window holds for unrelated wikis too (enwiki, dewiki checked
directly) with 2026-04-01 404ing on enwiki -- consistent with active pruning
to a rolling window, though no page documents a retention policy in writing;
this is an inference from the observed pattern, not a cited policy. This
script fetches
whichever of those aren't already downloaded (each into its own dump/<date>/
directory, never touching the live dump/*.xml used for routine `make process`
runs), processes each into a throwaway tree2.json-shaped snapshot, and runs
pipeline.compare2 pairwise across consecutive months, appending each diff to
docs2/data/changelog2.json.

Deliberately does NOT write docs2/data/tree2.json or docs2/VERSION -- those
reflect the live, current-month pipeline state, not a historical replay.
This calls process.py's internals directly rather than shelling out to
`python -m pipeline.process`, specifically to skip its unconditional
_stamp_data_version() call (see process.py:main), which would otherwise
overwrite docs2/VERSION with backfill dates.

Extending this further back would require the legacy XML dump format at a
different dumps.wikimedia.org path -- explicitly out of scope here (see
notes/sawikisource-scraper-spec.md's non-goals); this script only ever
walks whatever's currently listed under mediawiki_content_current.

Usage:
    python -m pipeline.backfill
    python -m pipeline.backfill --snapshot-dir /tmp/snapshots
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline import fetch as fetch_mod
from pipeline.build_tree import build_category_graph, build_main_tree, refile_category
from pipeline.parse_dump import parse_dump
from pipeline.process import build_tree_json, compute_all_content_sizes
from pipeline.transclusion import build_transclusion_map
from pipeline.compare2 import build_report, print_summary

BACKFILL_MONTHS = ["2026-05-01", "2026-06-01", "2026-07-01"]
DEFAULT_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump"
DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "dump" / "_backfill_snapshots"
DEFAULT_CHANGELOG = Path(__file__).resolve().parent.parent / "docs2" / "data" / "changelog2.json"

# The current live docs2/data/tree2.json already IS this month's processed
# snapshot (see docs2/VERSION's __content_version__) -- reuse it rather than
# re-fetching/re-processing a month we already have, as long as its
# __content_version__ actually matches. Falls back to a normal fetch+process
# if VERSION is missing/stale/doesn't match.
LIVE_TREE2_JSON = Path(__file__).resolve().parent.parent / "docs2" / "data" / "tree2.json"
LIVE_VERSION_FILE = Path(__file__).resolve().parent.parent / "docs2" / "VERSION"


def _live_content_version() -> str | None:
    if not LIVE_VERSION_FILE.exists():
        return None
    for line in LIVE_VERSION_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("__content_version__"):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def process_dump(xml_path: Path, workers: int | None = None) -> dict:
    """Same sequence as process.py's main(), minus writing docs2/tree2.json
    or stamping docs2/VERSION -- returns the tree dict in memory instead."""
    print(f"parsing {xml_path}", file=sys.stderr)
    dump_index = parse_dump(xml_path)
    cat_ns_name = dump_index.namespaces[dump_index.category_ns_id()]

    print("building Main-namespace tree...", file=sys.stderr)
    main_nodes = build_main_tree(dump_index.pages_by_ns[0])

    print("building category graph...", file=sys.stderr)
    graph = build_category_graph(dump_index.pages_by_ns[14], cat_ns_name)
    if graph.root_title not in graph.nodes:
        raise RuntimeError(f"root category '{graph.root_title}' not found in {xml_path}")

    refile_category(graph, "धर्मशास्त्रम्", new_parent_title="ग्रन्थाः", old_parent_title=graph.root_title)

    print("building transclusion map...", file=sys.stderr)
    transclusion_map = build_transclusion_map(dump_index.pages_by_ns[0])

    print("computing content sizes (this is the slow step)...", file=sys.stderr)
    content_index = compute_all_content_sizes(
        dump_index, transliterate=True, transclusion_map=transclusion_map, workers=workers,
    )

    print("assembling tree...", file=sys.stderr)
    return build_tree_json(dump_index, graph, main_nodes, transclusion_map, content_index)


def ensure_month(date_str: str, dump_root: Path) -> Path:
    """Fetch+decompress one month's export into dump_root/<date>/ if not already
    there, returning the path to its uncompressed XML."""
    out_dir = dump_root / date_str
    existing = sorted(out_dir.glob("sawikisource-*.xml")) if out_dir.exists() else []
    if existing:
        print(f"{date_str}: already fetched -> {existing[0]}", file=sys.stderr)
        return existing[0]
    paths = fetch_mod.fetch(out_dir=out_dir, date=date_str)
    xml_paths = [p for p in paths if p.suffix == ".xml"]
    if not xml_paths:
        raise RuntimeError(f"no .xml produced for {date_str}")
    return xml_paths[0]


def ensure_snapshot(date_str: str, xml_path: Path, snapshot_dir: Path, workers: int | None) -> Path:
    snapshot_path = snapshot_dir / f"tree2-{date_str}.json"
    if snapshot_path.exists():
        print(f"{date_str}: snapshot already built -> {snapshot_path}", file=sys.stderr)
        return snapshot_path
    if date_str == _live_content_version() and LIVE_TREE2_JSON.exists():
        print(f"{date_str}: matches live docs2/data/tree2.json's __content_version__, reusing it "
              f"instead of reprocessing", file=sys.stderr)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(LIVE_TREE2_JSON.read_text(encoding="utf-8"), encoding="utf-8")
        return snapshot_path
    tree = process_dump(xml_path, workers=workers)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{date_str}: wrote snapshot -> {snapshot_path}", file=sys.stderr)
    print(f"{date_str}: root stats: {tree['root']['stats']}", file=sys.stderr)
    return snapshot_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months", nargs="+", default=BACKFILL_MONTHS,
                     help=f"months to backfill, oldest first (default: {BACKFILL_MONTHS})")
    ap.add_argument("--dump-root", type=Path, default=DEFAULT_DUMP_ROOT,
                     help="directory under which each month gets its own dump/<date>/ subdir")
    ap.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR,
                     help="where to write per-month tree2.json-shaped snapshots (gitignored, throwaway)")
    ap.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG,
                     help="changelog2.json to append pairwise diffs to")
    ap.add_argument("--workers", type=int, default=None, help="worker processes for content-size computation")
    args = ap.parse_args()

    snapshots = []
    for date_str in args.months:
        xml_path = ensure_month(date_str, args.dump_root)
        snapshot_path = ensure_snapshot(date_str, xml_path, args.snapshot_dir, args.workers)
        snapshots.append((date_str, snapshot_path))

    for (old_date, old_snap), (new_date, new_snap) in zip(snapshots, snapshots[1:]):
        print(f"\n=== comparing {old_date} -> {new_date} ===", file=sys.stderr)
        report = build_report(old_snap, new_snap)
        print_summary(report)

        if args.changelog.exists():
            log = json.loads(args.changelog.read_text())
        else:
            log = []
        next_id = max((e.get("id", 0) for e in log), default=0) + 1
        entry = {
            "id": next_id,
            "date": f"{new_date}T00:00:00Z",
            "old_date": f"{old_date}T00:00:00Z",
            **report,
        }
        log.append(entry)
        args.changelog.parent.mkdir(parents=True, exist_ok=True)
        args.changelog.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
        print(f"appended changelog entry #{next_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
