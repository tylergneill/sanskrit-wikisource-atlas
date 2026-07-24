"""Backfill historical changelog entries from older monthly dump exports.

Four source eras, all handled here:

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
     Its volunteer pipeline for sawikisource stalled after 2022-05-01 though
     (confirmed live -- see MATERIALIZED_MONTHS below), leaving a real gap
     up to the live rolling window's own start (2025-11-20).

3. **Materialized era** (MATERIALIZED_MONTHS, _ensure_materialized_month):
   covers two separate gaps via the same on-demand mechanism, both driven off
   MATERIALIZED_MONTHS (a single flat list -- nothing downstream needs to
   distinguish which gap a given month came from):
   - The Internet-Archive/live-window gap (MATERIALIZED_START/END, 2022-06
     through 2025-10).
   - The earlier archival hole between Internet Archive's last dump before
     वर्गसर्वस्वम् existed (2011-10-13) and its first dump after real legacy
     coverage resumes (2014-07-01) -- FOURTH_ERA_START/END, 2012-01 through
     2014-06, bounded at 2012-01 since वर्गसर्वस्वम्'s earliest revision is
     2012-01-20 (see notes/fourth-source-era.md).
   No dump *file* exists for these months on any source, but
   pipeline/materialize_snapshots.py reconstructs one from
   sawikisource-latest-pages-meta-history.xml.bz2 (every surviving revision
   ever made) -- for a cutoff date D, the wiki's state at D is just, per
   page, the newest revision <= D. The ~533MB meta-history dump itself is
   downloaded once (auto-fetched on first need, cached at
   dump/_materialize_src/) and reused for every month in both gaps; each
   month's reconstruction is generated on demand, one at a time, the moment
   ensure_month needs it -- never all months up front, since each
   materialized XML runs 1-2GB and there's no reason to hold more than one
   on disk at a time (see cleanup_raw_dump, which now deletes a materialized
   month's XML right after its snapshot is written, same as every other
   era). See pipeline/materialize_snapshots.py's docstring for the known
   deviations from a genuine dump of that month.

For each month, ensure_month resolves an exact date to an era:
MATERIALIZED_MONTHS take priority (checked first, since Internet
Archive/the live rolling window are confirmed to never have these months --
querying them would just waste an API round-trip); otherwise legacy months
(< LEGACY_CUTOVER) go through pipeline.fetch_legacy (which itself picks
live-rolling-window vs. Internet Archive per month); current-era months go
through pipeline.fetch against mediawiki_content_current, unchanged from
before. Each raw dump lands in its own dump/<date>/, dump/_legacy/<date>/, or
dump/_materialized/<date>/ directory, never touching the live dump/*.xml
used for routine `make process` runs. Each month is processed into a
throwaway tree.json-shaped snapshot, and pipeline.compare runs pairwise
across consecutive months, appending each diff to docs/data/changelog.json.

Once a month's snapshot is written, its raw dump directory is deleted
immediately (cleanup_raw_dump) -- the multi-GB .xml/.bz2 export is never
read again afterward, only the snapshot is (by pipeline.compare, or by a
resumed run's ensure_snapshot existence check). Pass --keep-raw-dumps to
disable this and keep raw dumps around for inspection. This now includes
materialized-era months too: unlike the raw meta-history dump they're
reconstructed from (which stays cached, since re-downloading it is the
expensive part), a materialized month's XML is cheap to regenerate on
demand from that local cache, so there's no reason to keep it around after
its snapshot exists.

Deliberately does NOT write docs/data/tree.json or docs/VERSION -- those
reflect the live, current-month pipeline state, not a historical replay.
This calls process.py's internals directly rather than shelling out to
`python -m pipeline.process`, specifically to skip its unconditional
_stamp_data_version() call (see process.py:main), which would otherwise
overwrite docs/VERSION with backfill dates.

With no --months given, the default is the full available range: every
legacy month (queried live -- see default_months() -- rather than
hardcoded, since the underlying sources' own listings are the source of
truth for what's actually fetchable), every materialized gap month
(MATERIALIZED_MONTHS, reconstructed on demand as each is reached), plus the
3 current-era months.

For a smart, resumable, one-month-at-a-time walk through this whole range
(so results can be inspected incrementally rather than run in one long
batch), use `make backfill` / pipeline/run_backfill_sequence.sh instead of
calling this module directly with the full default range.

Regardless of the order --months lists dates in (run_backfill_sequence.sh
passes each step as `OLDER NEWER`), the actual fetch/snapshot work in
main() always happens newest-to-oldest -- so within any single invocation,
nothing older is ever fetched/processed before something newer. Only the
final pairwise-comparison step (cheap once every snapshot already exists)
runs in --months's given order, since that's what the changelog's
old_date/date pairing and existing_transitions dedup rely on.

Usage:
    python -m pipeline.backfill --months 2022-04-01 2022-05-01
    python -m pipeline.backfill --snapshot-dir /tmp/snapshots
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from pipeline import fetch as fetch_mod
from pipeline import fetch_legacy
from pipeline.build_tree import build_category_graph, build_main_tree, refile_category
from pipeline.parse_dump import parse_dump
from pipeline.process import build_tree_json, compute_all_content_sizes
from pipeline.transclusion import build_transclusion_map
from pipeline.compare import build_report, print_summary

# Below this date, months are fetched from the Internet Archive
# (pipeline.fetch_legacy) instead of mediawiki_content_current
# (pipeline.fetch) -- see module docstring. This is the format's launch
# date, not the rolling window's own start, so it never needs to move again
# even as the window itself slides forward -- see current_era_months() below
# for the part that actually tracks "what's live right now".
LEGACY_CUTOVER = "2026-05-01"


def current_era_months() -> list[str]:
    """Queries mediawiki_content_current live (via pipeline.fetch.find_export)
    for every complete month from LEGACY_CUTOVER through today, oldest first.
    Replaces a hardcoded CURRENT_ERA_MONTHS list, which went stale the moment
    a new month rolled into the live 3-month rolling window (e.g. once August
    has a complete export, a hardcoded "May/June/July" list would silently
    keep omitting August from every backfill run, and NEWEST_ANCHORED in
    run_backfill_sequence.sh would never catch up) -- see
    notes/about-page-fact-check.md's "deltas stop short of the live dump"
    investigation for the incident that surfaced this. Walks forward from
    LEGACY_CUTOVER rather than backward from today, since the window's exact
    width isn't guaranteed to stay 3 months forever and this reflects
    whatever Wikimedia is actually serving right now, not an assumed count."""
    months = []
    year, month = (int(p) for p in LEGACY_CUTOVER.split("-")[:2])
    today = _dt.date.today()
    while _dt.date(year, month, 1) <= today:
        date_str = f"{year:04d}-{month:02d}-01"
        if fetch_mod.find_export(date_str) is not None:
            months.append(date_str)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months

# Internet Archive's volunteer sawikisource pipeline stalled after 2022-05-01
# (confirmed live against archive.org's advancedsearch API -- both the plain
# sawikisource-* identifier family and Wikimedia's own documented
# non-incremental query return the same 119 items, none newer), and the live
# rolling window (pipeline.fetch_legacy's other source) only reaches back to
# 2025-11-20 -- leaving this span with no dump-file source at all. Filled via
# pipeline/materialize_snapshots.py instead: an on-demand, one-month-at-a-time
# reconstruction from sawikisource-latest-pages-meta-history.xml.bz2 (every
# surviving revision ever made, auto-downloaded once and cached -- see
# _ensure_materialize_source), which derives each month's full-state XML by
# taking, per page, the newest revision <= that month's cutoff. See that
# script's module docstring for the known deviations from a "real" dump of
# that month (deleted-page handling, title/namespace drift, heuristic
# redirect re-derivation).
MATERIALIZED_START = "2022-06-01"
MATERIALIZED_END = "2025-10-01"

# Second, earlier materialized gap: Internet Archive's earliest two
# sawikisource dumps (2011-09-04, 2011-10-13) predate वर्गसर्वस्वम् itself
# (RootCategoryMissing, correctly skipped -- see process_dump), but its
# *next* dump doesn't appear until 2014-07 -- a real ~2.5-year archival hole,
# not specific to this mirror. Checking the same cached meta-history dump
# used above shows वर्गसर्वस्वम्'s earliest revision is 2012-01-20T10:18:19Z,
# so the category system this mirror's tree model depends on was already up
# and running for the back half of that hole. Materializing 2012-01 through
# 2014-06 (bounded at 2012-01 since the category genuinely doesn't exist
# before that) recovers real historical growth the changelog would otherwise
# silently show nothing for. See notes/fourth-source-era.md for the
# investigation this range is based on.
FOURTH_ERA_START = "2012-01-01"
FOURTH_ERA_END = "2014-06-01"

MATERIALIZED_MONTHS = [
    f"{y:04d}-{m:02d}-01"
    for y in range(2012, 2026)
    for m in range(1, 13)
    if (MATERIALIZED_START <= f"{y:04d}-{m:02d}-01" <= MATERIALIZED_END)
    or (FOURTH_ERA_START <= f"{y:04d}-{m:02d}-01" <= FOURTH_ERA_END)
]

MATERIALIZE_SOURCE_URL = (
    "https://dumps.wikimedia.org/sawikisource/latest/"
    "sawikisource-latest-pages-meta-history.xml.bz2"
)

DEFAULT_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump"
DEFAULT_LEGACY_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump" / "_legacy"
DEFAULT_MATERIALIZED_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump" / "_materialized"
DEFAULT_MATERIALIZE_SRC_DIR = Path(__file__).resolve().parent.parent / "dump" / "_materialize_src"
DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "dump" / "_backfill_snapshots"
DEFAULT_CHANGELOG = Path(__file__).resolve().parent.parent / "docs" / "data" / "changelog.json"

# The current live docs/data/tree.json already IS this month's processed
# snapshot (see docs/VERSION's __content_version__) -- reuse it rather than
# re-fetching/re-processing a month we already have, as long as its
# __content_version__ actually matches. Falls back to a normal fetch+process
# if VERSION is missing/stale/doesn't match.
LIVE_TREE_JSON = Path(__file__).resolve().parent.parent / "docs" / "data" / "tree.json"
LIVE_VERSION_FILE = Path(__file__).resolve().parent.parent / "docs" / "VERSION"


def _live_content_version() -> str | None:
    if not LIVE_VERSION_FILE.exists():
        return None
    for line in LIVE_VERSION_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("__content_version__"):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


class RootCategoryMissing(Exception):
    """Raised by process_dump when the mirror's organizing root category
    (वर्गसर्वस्वम्) isn't present in a dump at all -- not a parse error, but a
    real historical fact about sa.wikisource: this whole mirror's tree model
    depends on that category existing, and it didn't yet in the site's
    earliest days (confirmed on the 2011-10-13 Internet Archive dump: only 3
    categories existed on the entire site at that point, none of them
    वर्गसर्वस्वम्). Callers should treat this as "too early to build a
    snapshot for," skipping the month rather than crashing the whole run."""


def process_dump(xml_path: Path, workers: int | None = None) -> dict:
    """Same sequence as process.py's main(), minus writing docs/tree.json
    or stamping docs/VERSION -- returns the tree dict in memory instead."""
    print(f"parsing {xml_path}", file=sys.stderr)
    dump_index = parse_dump(xml_path)
    cat_ns_name = dump_index.namespaces[dump_index.category_ns_id()]

    print("building Main-namespace tree...", file=sys.stderr)
    main_nodes = build_main_tree(dump_index.pages_by_ns[0])

    print("building category graph...", file=sys.stderr)
    graph = build_category_graph(dump_index.pages_by_ns[14], cat_ns_name)
    if graph.root_title not in graph.nodes:
        raise RootCategoryMissing(
            f"root category '{graph.root_title}' not found in {xml_path} -- "
            f"too early in sa.wikisource's history for this mirror's tree model"
        )

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
    window and Internet Archive -- see fetch_legacy.list_available_months),
    every materialized gap month (MATERIALIZED_MONTHS -- always included,
    since materialization now happens on demand rather than depending on
    pre-existing disk output, see _ensure_materialized_month), plus every
    current-era month queried live (current_era_months() -- NOT a hardcoded
    count, since the live rolling window's contents shift forward over
    time), oldest first -- the full available range absent an explicit
    --months override."""
    legacy_months = sorted(fetch_legacy.list_available_months())
    legacy_dates = [f"{ym}-01" for ym in legacy_months]
    materialized_dates = [d for d in MATERIALIZED_MONTHS if d not in legacy_dates]
    return sorted(set(legacy_dates) | set(materialized_dates)) + current_era_months()


def ensure_month(
    date_str: str,
    dump_root: Path,
    legacy_dump_root: Path,
    materialized_dump_root: Path = DEFAULT_MATERIALIZED_DUMP_ROOT,
    materialize_src_dir: Path = DEFAULT_MATERIALIZE_SRC_DIR,
) -> Path:
    """Fetch+decompress one month's export if not already there, returning the
    path to its uncompressed XML. Dispatches to _ensure_materialized_month
    (MATERIALIZED_MONTHS, materialized_dump_root/<date>/) for the
    Internet-Archive/live-window gap, checked first since neither of
    pipeline.fetch_legacy's sources has ever had these months (confirmed live
    against archive.org -- see MATERIALIZED_MONTHS); otherwise
    pipeline.fetch_legacy (legacy era, legacy_dump_root/<date>/) or
    pipeline.fetch (current era, mediawiki_content_current, dump_root/<date>/)
    based on LEGACY_CUTOVER, unchanged from before."""
    if date_str in MATERIALIZED_MONTHS:
        return _ensure_materialized_month(date_str, materialized_dump_root, materialize_src_dir)

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


def _ensure_materialize_source(materialize_src_dir: Path) -> Path:
    """Download sawikisource-latest-pages-meta-history.xml.bz2 to
    materialize_src_dir once (skipped if already present) -- this is the raw
    material every MATERIALIZED_MONTHS reconstruction is generated from, so
    it's the one thing in the materialized era worth caching indefinitely
    rather than deleting after use (see cleanup_raw_dump, which no longer
    touches this file). Reuses pipeline.fetch_legacy's session/User-Agent
    since both hit dumps.wikimedia.org under the same bot-etiquette
    contract (maxlag / compliant UA -- see CLAUDE.md's rate-limiting notes)."""
    materialize_src_dir.mkdir(parents=True, exist_ok=True)
    dest = materialize_src_dir / "sawikisource-latest-pages-meta-history.xml.bz2"
    if dest.exists():
        return dest
    print(f"downloading {MATERIALIZE_SOURCE_URL} (~530MB, one-time)...", file=sys.stderr)
    tmp_dest = dest.with_suffix(dest.suffix + ".tmp")
    with fetch_legacy.session.get(MATERIALIZE_SOURCE_URL, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(tmp_dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)
    tmp_dest.rename(dest)
    print(f"downloaded: {dest}", file=sys.stderr)
    return dest


def _ensure_materialized_month(
    date_str: str, materialized_dump_root: Path, materialize_src_dir: Path
) -> Path:
    """Reconstruct date_str's snapshot XML on demand via
    pipeline/materialize_snapshots.py, one month at a time -- never all of
    MATERIALIZED_MONTHS up front, since each output runs 1-2GB and
    cleanup_raw_dump deletes it right after its tree.json snapshot is
    written (see module docstring). Returns the existing output directly if
    this date was already materialized and not yet cleaned up (e.g. a
    resumed run)."""
    day8 = date_str.replace("-", "")
    item_dir = materialized_dump_root / date_str
    existing = sorted(item_dir.glob(f"sawikisource-{day8}-pages-articles.synth.xml")) if item_dir.exists() else []
    if existing:
        print(f"{date_str}: already materialized -> {existing[0]}", file=sys.stderr)
        return existing[0]

    src_bz2 = _ensure_materialize_source(materialize_src_dir)
    item_dir.mkdir(parents=True, exist_ok=True)
    print(f"{date_str}: materializing from {src_bz2}...", file=sys.stderr)
    subprocess.run(
        [
            sys.executable, str(Path(__file__).resolve().parent / "materialize_snapshots.py"),
            str(src_bz2), "--dates", date_str, "--outdir", str(item_dir),
        ],
        check=True,
    )
    xml_path = item_dir / f"sawikisource-{day8}-pages-articles.synth.xml"
    if not xml_path.exists():
        raise RuntimeError(f"materialize_snapshots.py did not produce {xml_path}")
    print(f"{date_str}: materialized -> {xml_path}", file=sys.stderr)
    return xml_path


def cleanup_raw_dump(
    date_str: str,
    dump_root: Path,
    legacy_dump_root: Path,
    materialized_dump_root: Path = DEFAULT_MATERIALIZED_DUMP_ROOT,
) -> None:
    """Delete the raw dump (.xml.bz2 + decompressed .xml, and their parent
    dated directory) for one month, once its snapshot is confirmed written --
    the snapshot is all that pipeline.compare or a resumed backfill run ever
    reads afterward (see ensure_snapshot's existence check), so keeping the
    multi-GB raw export around after that point is pure disk waste. Never
    touches dump_root's own top-level loose files (the live current-month
    dump used by routine `make process`) -- only the dated subdirectories
    this module itself creates via ensure_month.

    Applies to MATERIALIZED_MONTHS too: a materialized month's XML is
    cheaply regenerable on demand from the cached meta-history dump (see
    _ensure_materialize_source), so there's no reason to keep it around
    after its snapshot exists -- unlike the cached meta-history .bz2 itself,
    which this function never touches."""
    if date_str in MATERIALIZED_MONTHS:
        d = materialized_dump_root / date_str
        if d.is_dir():
            shutil.rmtree(d)
            print(f"{date_str}: deleted materialized dump -> {d}", file=sys.stderr)
        return
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
    snapshot_path = snapshot_dir / f"tree-{date_str}.json"
    if snapshot_path.exists():
        print(f"{date_str}: snapshot already built -> {snapshot_path}", file=sys.stderr)
        return snapshot_path
    if date_str == _live_content_version() and LIVE_TREE_JSON.exists():
        print(f"{date_str}: matches live docs/data/tree.json's __content_version__, reusing it "
              f"instead of reprocessing", file=sys.stderr)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(LIVE_TREE_JSON.read_text(encoding="utf-8"), encoding="utf-8")
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
                          "range -- every Internet Archive month, every materialized gap month "
                          "(reconstructed on demand), plus the 3 current-era months, queried live)")
    ap.add_argument("--dump-root", type=Path, default=DEFAULT_DUMP_ROOT,
                     help="directory under which each current-era month gets its own dump/<date>/ subdir")
    ap.add_argument("--legacy-dump-root", type=Path, default=DEFAULT_LEGACY_DUMP_ROOT,
                     help="directory under which each legacy-era (Internet Archive) month gets its own subdir")
    ap.add_argument("--materialized-dump-root", type=Path, default=DEFAULT_MATERIALIZED_DUMP_ROOT,
                     help="directory where each MATERIALIZED_MONTHS date (the Internet-Archive/live-window "
                          "gap) gets its own subdir, generated on demand one month at a time and deleted "
                          "again once its snapshot is written (see _ensure_materialized_month)")
    ap.add_argument("--materialize-src-dir", type=Path, default=DEFAULT_MATERIALIZE_SRC_DIR,
                     help="directory to cache the ~530MB sawikisource-latest-pages-meta-history.xml.bz2 "
                          "in, auto-downloaded once on first need and reused for every materialized month")
    ap.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR,
                     help="where to write per-month tree.json-shaped snapshots (gitignored, throwaway)")
    ap.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG,
                     help="changelog.json to append pairwise diffs to")
    ap.add_argument("--workers", type=int, default=None, help="worker processes for content-size computation")
    ap.add_argument("--keep-raw-dumps", action="store_true",
                     help="don't delete each month's raw dump (.xml/.bz2) after its snapshot is written -- "
                          "by default, raw dumps are deleted immediately since the snapshot is all that's "
                          "ever needed afterward (see cleanup_raw_dump)")
    args = ap.parse_args()

    months = args.months if args.months is not None else default_months()

    # Fetch/snapshot newest-first, regardless of the order months was given
    # in -- e.g. `--months OLDER NEWER` must not process OLDER before NEWER.
    # This matters because ensure_month/ensure_snapshot are the only steps
    # that do real work (network fetch, dump parsing, content-size
    # computation); the pairwise comparison below is comparatively instant
    # once every snapshot already exists, so it's fine to do in whatever
    # order months was given, and preserving that original order is what
    # existing_transitions/changelog entries below expect.
    snapshots_by_date = {}
    for date_str in sorted(months, reverse=True):
        get_xml_path = lambda d=date_str: ensure_month(
            d, args.dump_root, args.legacy_dump_root, args.materialized_dump_root, args.materialize_src_dir
        )
        try:
            snapshot_path = ensure_snapshot(date_str, get_xml_path, args.snapshot_dir, args.workers)
        except RootCategoryMissing as e:
            # Too early in sa.wikisource's history for this mirror's tree
            # model (see RootCategoryMissing) -- skip this month rather than
            # aborting the whole run; any comparison pairs involving it are
            # dropped below.
            print(f"{date_str}: {e} -- skipping this month", file=sys.stderr)
            if not args.keep_raw_dumps:
                cleanup_raw_dump(date_str, args.dump_root, args.legacy_dump_root, args.materialized_dump_root)
            continue
        snapshots_by_date[date_str] = snapshot_path
        if not args.keep_raw_dumps:
            cleanup_raw_dump(date_str, args.dump_root, args.legacy_dump_root, args.materialized_dump_root)

    snapshots = [(date_str, snapshots_by_date[date_str]) for date_str in months if date_str in snapshots_by_date]

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
        # leave the file (and about.js's newest-first reversal of it) out
        # of chronological order. `id` stays a stable append-order identifier,
        # untouched by this re-sort.
        log.sort(key=lambda e: e["date"])
        args.changelog.parent.mkdir(parents=True, exist_ok=True)
        args.changelog.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
        print(f"appended changelog entry #{next_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
