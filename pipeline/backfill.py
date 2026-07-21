"""Backfill historical docs2 changelog entries from older monthly dump exports.

Two source eras, both handled here:

1. **Current era** (pipeline.fetch / mediawiki_content_current): only a
   3-month rolling window is available online for sawikisource --
   2026-05-01, 2026-06-01, 2026-07-01 as of this writing. The format itself
   launched 2026-01-30 (announced on xmldatadumps-l), and the same 3-month
   window holds for unrelated wikis too (enwiki, dewiki checked directly)
   with 2026-04-01 404ing on enwiki -- consistent with active pruning to a
   rolling window, though no page documents a retention policy in writing;
   this is an inference from the observed pattern, not a cited policy.

2. **Legacy era** (pipeline.fetch_legacy): the classic MediaWiki export
   format (pages-meta-current.xml.bz2). Two sources, merged transparently by
   fetch_legacy.list_available_months -- pipeline.backfill never needs to
   know which one served a given month:
   - **Live rolling window** (dumps.wikimedia.org/sawikisource/<date>/):
     Wikimedia's own still-current copy of the legacy format. Also just a
     rolling window (confirmed directly), but as of this writing it holds
     2025-11-20 through 2026-07-01 -- i.e. it reaches right up to (and
     overlaps with) mediawiki_content_current's start, which is what fills
     what would otherwise be a ~4-year gap (2022-05 to 2026-05) between
     Internet Archive's last snapshot and the current-era window's first.
   - **Internet Archive** (item `sawikisource-<YYYYMMDD>`, one per
     historical dump run): the only source with real historical depth, back
     to 2011 -- confirmed via a spike run against 2022-01-20 that
     pipeline.parse_dump / build_tree / transclusion / content_size all read
     this format with zero code changes (same schema, same namespace IDs).

For each month, ensure_month resolves an exact date to an era: legacy months
(< LEGACY_CUTOVER) go through pipeline.fetch_legacy (which itself picks
live-rolling-window vs. Internet Archive per month); current-era months go
through pipeline.fetch against mediawiki_content_current, unchanged from
before. Each raw dump lands in its own dump/<date>/ or dump/_legacy/<date>/
directory, never touching the live dump/*.xml used for routine `make
process` runs. Each month is processed into a throwaway tree2.json-shaped
snapshot, and pipeline.compare2 runs pairwise across consecutive months,
appending each diff to docs2/data/changelog2.json.

Once a month's snapshot is written, its raw dump directory is deleted
immediately (cleanup_raw_dump) -- the multi-GB .xml/.bz2 export is never
read again afterward, only the snapshot is (by pipeline.compare2, or by a
resumed run's ensure_snapshot existence check). Pass --keep-raw-dumps to
disable this and keep raw dumps around for inspection.

Deliberately does NOT write docs2/data/tree2.json or docs2/VERSION -- those
reflect the live, current-month pipeline state, not a historical replay.
This calls process.py's internals directly rather than shelling out to
`python -m pipeline.process`, specifically to skip its unconditional
_stamp_data_version() call (see process.py:main), which would otherwise
overwrite docs2/VERSION with backfill dates.

With no --months given, the default is the full available range: every
legacy month (queried live -- see default_months() -- rather than
hardcoded, since the underlying sources' own listings are the source of
truth for what's actually fetchable) plus the 3 current-era months.

For a smart, resumable, one-month-at-a-time walk through this whole range
(so results can be inspected incrementally rather than run in one long
batch), use `make backfill` / pipeline/run_backfill_sequence.sh instead of
calling this module directly with the full default range.

Usage:
    python -m pipeline.backfill --months 2022-04-01 2022-05-01
    python -m pipeline.backfill --snapshot-dir /tmp/snapshots
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Callable

from pipeline import fetch as fetch_mod
from pipeline import fetch_legacy
from pipeline.build_tree import build_category_graph, build_main_tree, refile_category
from pipeline.parse_dump import parse_dump
from pipeline.process import build_tree_json, compute_all_content_sizes
from pipeline.transclusion import build_transclusion_map
from pipeline.compare2 import build_report, print_summary

# Below this date, months are fetched from the Internet Archive
# (pipeline.fetch_legacy) instead of mediawiki_content_current
# (pipeline.fetch) -- see module docstring.
LEGACY_CUTOVER = "2026-05-01"

CURRENT_ERA_MONTHS = ["2026-05-01", "2026-06-01", "2026-07-01"]
DEFAULT_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump"
DEFAULT_LEGACY_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump" / "_legacy"
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


def default_months() -> list[str]:
    """Every legacy month (queried live, merged across the live rolling
    window and Internet Archive -- see fetch_legacy.list_available_months)
    plus the 3 current-era months, oldest first -- the full available range
    absent an explicit --months override."""
    legacy_months = sorted(fetch_legacy.list_available_months())
    return [f"{ym}-01" for ym in legacy_months] + CURRENT_ERA_MONTHS


def ensure_month(date_str: str, dump_root: Path, legacy_dump_root: Path) -> Path:
    """Fetch+decompress one month's export if not already there, returning the
    path to its uncompressed XML. Dispatches to pipeline.fetch (current era,
    mediawiki_content_current, dump_root/<date>/) or pipeline.fetch_legacy
    (legacy era, Internet Archive, legacy_dump_root/<date>/) based on
    LEGACY_CUTOVER."""
    if date_str < LEGACY_CUTOVER:
        return _ensure_legacy_month(date_str, legacy_dump_root)

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


def _ensure_legacy_month(date_str: str, legacy_dump_root: Path) -> Path:
    """date_str is the requested YYYY-MM-01 (the calendar month, used as this
    entry's identity throughout backfill/changelog). The actual underlying
    snapshot within that month can fall on any day and come from either
    source (e.g. 2022-01-20 from Internet Archive, or 2026-04-01 from the
    live rolling window -- see fetch_legacy.list_available_months) --
    fetch_legacy.fetch_snapshot writes into a directory named after that real
    day, so this looks inside legacy_dump_root/<ym>-*/ (a glob on the month
    prefix) rather than assuming day 01."""
    ym = date_str[:7]
    existing = sorted(legacy_dump_root.glob(f"{ym}-*/sawikisource-*.xml"))
    if existing:
        print(f"{date_str}: already fetched (legacy) -> {existing[0]}", file=sys.stderr)
        return existing[0]

    by_month = fetch_legacy.list_available_months()
    dump = by_month.get(ym)
    if dump is None:
        raise RuntimeError(f"no legacy snapshot found for month {ym} (date {date_str})")
    return fetch_legacy.fetch_snapshot(dump, out_dir=legacy_dump_root)


def cleanup_raw_dump(date_str: str, dump_root: Path, legacy_dump_root: Path) -> None:
    """Delete the raw dump (.xml.bz2 + decompressed .xml, and their parent
    dated directory) for one month, once its snapshot is confirmed written --
    the snapshot is all that pipeline.compare2 or a resumed backfill run ever
    reads afterward (see ensure_snapshot's existence check), so keeping the
    multi-GB raw export around after that point is pure disk waste. Never
    touches dump_root's own top-level loose files (the live current-month
    dump used by routine `make process`) -- only the dated subdirectories
    this module itself creates via ensure_month."""
    if date_str < LEGACY_CUTOVER:
        ym = date_str[:7]
        for d in sorted(legacy_dump_root.glob(f"{ym}-*")):
            if d.is_dir():
                shutil.rmtree(d)
                print(f"{date_str}: deleted raw dump -> {d}", file=sys.stderr)
    else:
        d = dump_root / date_str
        if d.is_dir():
            shutil.rmtree(d)
            print(f"{date_str}: deleted raw dump -> {d}", file=sys.stderr)


def ensure_snapshot(
    date_str: str,
    get_xml_path: Callable[[], Path],
    snapshot_dir: Path,
    workers: int | None,
) -> Path:
    """get_xml_path is called (triggering ensure_month's fetch/decompress)
    only if a snapshot doesn't already exist and can't be reused from the
    live tree either -- so an already-completed month's raw dump, which
    cleanup_raw_dump deletes right after its snapshot is written, is never
    re-fetched on a resumed run just to be thrown away again."""
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
    xml_path = get_xml_path()
    tree = process_dump(xml_path, workers=workers)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{date_str}: wrote snapshot -> {snapshot_path}", file=sys.stderr)
    print(f"{date_str}: root stats: {tree['root']['stats']}", file=sys.stderr)
    return snapshot_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months", nargs="+", default=None,
                     help="months to backfill, oldest first, as YYYY-MM-01 (default: full available "
                          "range -- every Internet Archive month plus the 3 current-era months, "
                          "queried live)")
    ap.add_argument("--dump-root", type=Path, default=DEFAULT_DUMP_ROOT,
                     help="directory under which each current-era month gets its own dump/<date>/ subdir")
    ap.add_argument("--legacy-dump-root", type=Path, default=DEFAULT_LEGACY_DUMP_ROOT,
                     help="directory under which each legacy-era (Internet Archive) month gets its own subdir")
    ap.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR,
                     help="where to write per-month tree2.json-shaped snapshots (gitignored, throwaway)")
    ap.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG,
                     help="changelog2.json to append pairwise diffs to")
    ap.add_argument("--workers", type=int, default=None, help="worker processes for content-size computation")
    ap.add_argument("--keep-raw-dumps", action="store_true",
                     help="don't delete each month's raw dump (.xml/.bz2) after its snapshot is written -- "
                          "by default, raw dumps are deleted immediately since the snapshot is all that's "
                          "ever needed afterward (see cleanup_raw_dump)")
    args = ap.parse_args()

    months = args.months if args.months is not None else default_months()

    snapshots = []
    for date_str in months:
        get_xml_path = lambda d=date_str: ensure_month(d, args.dump_root, args.legacy_dump_root)
        snapshot_path = ensure_snapshot(date_str, get_xml_path, args.snapshot_dir, args.workers)
        snapshots.append((date_str, snapshot_path))
        if not args.keep_raw_dumps:
            cleanup_raw_dump(date_str, args.dump_root, args.legacy_dump_root)

    if args.changelog.exists():
        log = json.loads(args.changelog.read_text())
    else:
        log = []
    existing_transitions = {(e.get("old_date"), e.get("date")) for e in log}

    for (old_date, old_snap), (new_date, new_snap) in zip(snapshots, snapshots[1:]):
        old_iso, new_iso = f"{old_date}T00:00:00Z", f"{new_date}T00:00:00Z"
        if (old_iso, new_iso) in existing_transitions:
            print(f"\n=== {old_date} -> {new_date}: already in changelog, skipping ===", file=sys.stderr)
            continue

        print(f"\n=== comparing {old_date} -> {new_date} ===", file=sys.stderr)
        report = build_report(old_snap, new_snap)
        print_summary(report)

        next_id = max((e.get("id", 0) for e in log), default=0) + 1
        entry = {
            "id": next_id,
            "date": new_iso,
            "old_date": old_iso,
            **report,
        }
        log.append(entry)
        existing_transitions.add((old_iso, new_iso))
        # Sort by date on every write (not just append) -- entries are
        # computed/appended in whatever order --months was given, and a
        # backfill run mixing legacy and current-era months would otherwise
        # leave the file (and about2.js's newest-first reversal of it) out
        # of chronological order. `id` stays a stable append-order identifier,
        # untouched by this re-sort.
        log.sort(key=lambda e: e["date"])
        args.changelog.parent.mkdir(parents=True, exist_ok=True)
        args.changelog.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
        print(f"appended changelog entry #{next_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
