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
     2012-01-20.
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
before. Each raw dump lands in its own dated subdir of the appropriate
numbered era folder (dump/1_current_format_live/<date>/,
dump/2_legacy_format_live/<date>/, dump/3_materialized_2022_2025/<date>/,
dump/4_legacy_format_archive/<date>/, or dump/5_materialized_2012_2014/<date>/
-- see the DEFAULT_*_ROOT constants below), never touching any other era's
folder. Each month is processed into a
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
from pipeline.content_cache import build_content_cache, load_content_cache, rebuild_inputs_from_cache, write_content_cache
from pipeline.parse_dump import parse_dump
from pipeline.process import build_tree_json, compute_all_content_sizes
from pipeline.snapshot_io import write_json_gz
from pipeline.transclusion import build_reverse_transclusion_map, build_transclusion_map
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
# silently show nothing for.
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

# Five numbered era folders under dump/, in the same newest-to-oldest order
# run_backfill_sequence.sh walks in -- see notes on each era above and in
# CLAUDE.md's "Historical backfill and the changelog" section:
#   1_current_format_live      -- pipeline.fetch, mediawiki_content_current
#   2_legacy_format_live       -- pipeline.fetch_legacy, live rolling window
#   3_materialized_2022_2025   -- MATERIALIZED_START/END gap
#   4_legacy_format_archive    -- pipeline.fetch_legacy, Internet Archive
#   5_materialized_2012_2014   -- FOURTH_ERA_START/END gap
DEFAULT_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump" / "1_current_format_live"
DEFAULT_LEGACY_LIVE_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump" / "2_legacy_format_live"
DEFAULT_MATERIALIZED_GAP_A_ROOT = Path(__file__).resolve().parent.parent / "dump" / "3_materialized_2022_2025"
DEFAULT_LEGACY_ARCHIVE_DUMP_ROOT = Path(__file__).resolve().parent.parent / "dump" / "4_legacy_format_archive"
DEFAULT_MATERIALIZED_GAP_B_ROOT = Path(__file__).resolve().parent.parent / "dump" / "5_materialized_2012_2014"
DEFAULT_MATERIALIZE_SRC_DIR = Path(__file__).resolve().parent.parent / "dump" / "_materialize_src"
DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "dump" / "_backfill_snapshots"
DEFAULT_CONTENT_CACHE_DIR = Path(__file__).resolve().parent.parent / "dump" / "_backfill_content_cache"
DEFAULT_CHANGELOG = Path(__file__).resolve().parent.parent / "docs" / "data" / "changelog.json"


def _is_gap_a(date_str: str) -> bool:
    return MATERIALIZED_START <= date_str <= MATERIALIZED_END


def _is_gap_b(date_str: str) -> bool:
    return FOURTH_ERA_START <= date_str <= FOURTH_ERA_END


def _materialized_root(date_str: str, gap_a_root: Path, gap_b_root: Path) -> Path:
    """Routes a MATERIALIZED_MONTHS date to its era-specific folder -- gap A
    (2022-2025, the Internet-Archive/live-window gap) or gap B (2012-2014,
    the earlier pre-legacy-coverage gap) -- based on which range constant it
    falls in, same membership check MATERIALIZED_MONTHS itself is built
    from."""
    if _is_gap_a(date_str):
        return gap_a_root
    if _is_gap_b(date_str):
        return gap_b_root
    raise ValueError(f"{date_str} is not in either materialized gap")

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


def process_dump(xml_path: Path, workers: int | None = None) -> tuple[dict, dict]:
    """Same sequence as process.py's main(), minus writing docs/tree.json or
    stamping docs/VERSION -- returns (tree, content_cache) in memory instead.
    content_cache is the small cache of build_tree_json's inputs (see
    pipeline.content_cache) that lets a future build_tree_json-only logic fix
    skip re-running the expensive steps below (parse_dump, compute_all_content_sizes)."""
    print(f"parsing {xml_path}", file=sys.stderr)
    dump_index = parse_dump(xml_path)
    cat_ns_name = dump_index.namespaces[dump_index.category_ns_id()]

    print("building Main-namespace tree...", file=sys.stderr)
    main_records = dump_index.pages_by_ns[0]
    main_nodes = build_main_tree(main_records)

    print("building category graph...", file=sys.stderr)
    category_records = dump_index.pages_by_ns[14]
    graph = build_category_graph(category_records, cat_ns_name)
    if graph.root_title not in graph.nodes:
        raise RootCategoryMissing(
            f"root category '{graph.root_title}' not found in {xml_path} -- "
            f"too early in sa.wikisource's history for this mirror's tree model"
        )

    refile_category(graph, "धर्मशास्त्रम्", new_parent_title="ग्रन्थाः", old_parent_title=graph.root_title)

    print("building transclusion map...", file=sys.stderr)
    transclusion_map = build_transclusion_map(main_records)
    reverse_transclusion_map = build_reverse_transclusion_map(main_records)

    print("computing content sizes (this is the slow step)...", file=sys.stderr)
    content_index = compute_all_content_sizes(
        dump_index, transliterate=True, transclusion_map=transclusion_map, workers=workers,
    )

    print("assembling tree...", file=sys.stderr)
    tree = build_tree_json(dump_index, graph, main_nodes, transclusion_map, content_index, reverse_transclusion_map)
    content_cache = build_content_cache(dump_index, content_index, main_records, category_records)
    return tree, content_cache


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
    legacy_live_dump_root: Path,
    legacy_archive_dump_root: Path,
    materialized_gap_a_root: Path = DEFAULT_MATERIALIZED_GAP_A_ROOT,
    materialized_gap_b_root: Path = DEFAULT_MATERIALIZED_GAP_B_ROOT,
    materialize_src_dir: Path = DEFAULT_MATERIALIZE_SRC_DIR,
) -> Path:
    """Fetch+decompress one month's export if not already there, returning the
    path to its uncompressed XML. Dispatches to _ensure_materialized_month
    (MATERIALIZED_MONTHS, routed to materialized_gap_a_root or
    materialized_gap_b_root by _materialized_root) for either
    materialization gap, checked first since neither of
    pipeline.fetch_legacy's sources has ever had these months (confirmed live
    against archive.org -- see MATERIALIZED_MONTHS); otherwise
    pipeline.fetch_legacy (legacy era -- routed to legacy_live_dump_root or
    legacy_archive_dump_root by _ensure_legacy_month based on which source
    served the month) or pipeline.fetch (current era, mediawiki_content_current,
    dump_root/<date>/) based on LEGACY_CUTOVER, unchanged from before."""
    if date_str in MATERIALIZED_MONTHS:
        materialized_dump_root = _materialized_root(date_str, materialized_gap_a_root, materialized_gap_b_root)
        return _ensure_materialized_month(date_str, materialized_dump_root, materialize_src_dir)

    if date_str < LEGACY_CUTOVER:
        return _ensure_legacy_month(date_str, legacy_live_dump_root, legacy_archive_dump_root)

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


def _ensure_legacy_month(date_str: str, legacy_live_dump_root: Path, legacy_archive_dump_root: Path) -> Path:
    """date_str is the requested YYYY-MM-01 (the calendar month, used as this
    entry's identity throughout backfill/changelog). The actual underlying
    snapshot within that month can fall on any day and come from either
    source (e.g. 2022-01-20 from Internet Archive, or 2026-04-01 from the
    live rolling window -- see fetch_legacy.list_available_months), which
    also determines which of the two era-specific roots it belongs under --
    fetch_legacy.fetch_snapshot writes into a directory named after that real
    day, so this looks inside <root>/<ym>-*/ (a glob on the month prefix)
    rather than assuming day 01. Checks both roots for an already-fetched
    month, since which source serves a given date can shift over time as the
    live window's floor drifts forward (see module docstring)."""
    ym = date_str[:7]
    for root in (legacy_live_dump_root, legacy_archive_dump_root):
        existing = sorted(root.glob(f"{ym}-*/sawikisource-*.xml"))
        if existing:
            print(f"{date_str}: already fetched (legacy) -> {existing[0]}", file=sys.stderr)
            return existing[0]

    by_month = fetch_legacy.list_available_months()
    dump = by_month.get(ym)
    if dump is None:
        raise RuntimeError(f"no legacy snapshot found for month {ym} (date {date_str})")
    out_dir = legacy_live_dump_root if dump.source == "live" else legacy_archive_dump_root
    return fetch_legacy.fetch_snapshot(dump, out_dir=out_dir)


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


def _is_complete_materialized_xml(path: Path) -> bool:
    """A materialize_snapshots.py output is only trustworthy if it was fully
    written -- SnapshotWriter.close() now writes to the real path via an
    atomic rename (see that class), but a file materialized before that fix
    landed could still be sitting on disk, truncated (interrupted mid-write,
    file present but incomplete). Cheap check: the closing </mediawiki> tag
    is only ever written by close(), right before the rename, so its
    presence at the tail of the file is a reliable completeness signal
    without re-parsing the whole XML."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - 64))
            tail = f.read()
        return b"</mediawiki>" in tail
    except OSError:
        return False


def _ensure_materialized_month(
    date_str: str, materialized_dump_root: Path, materialize_src_dir: Path
) -> Path:
    """Reconstruct date_str's snapshot XML on demand via
    pipeline/materialize_snapshots.py, one month at a time -- never all of
    MATERIALIZED_MONTHS up front, since each output runs 1-2GB and
    cleanup_raw_dump deletes it right after its tree.json snapshot is
    written (see module docstring). Returns the existing output directly if
    this date was already materialized and not yet cleaned up (e.g. a
    resumed run) AND it looks complete (see _is_complete_materialized_xml)
    -- an incomplete file found on disk (e.g. from before
    SnapshotWriter's atomic-rename fix) is deleted and regenerated rather
    than silently reused, since a truncated file merely being present used
    to pass this check and crash pipeline.parse_dump much later, deep into
    a backfill sequence."""
    day8 = date_str.replace("-", "")
    item_dir = materialized_dump_root / date_str
    existing = sorted(item_dir.glob(f"sawikisource-{day8}-pages-articles.synth.xml")) if item_dir.exists() else []
    if existing:
        if _is_complete_materialized_xml(existing[0]):
            print(f"{date_str}: already materialized -> {existing[0]}", file=sys.stderr)
            return existing[0]
        print(f"{date_str}: found incomplete materialized XML at {existing[0]} -- deleting and regenerating",
              file=sys.stderr)
        existing[0].unlink()

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
    legacy_live_dump_root: Path,
    legacy_archive_dump_root: Path,
    materialized_gap_a_root: Path = DEFAULT_MATERIALIZED_GAP_A_ROOT,
    materialized_gap_b_root: Path = DEFAULT_MATERIALIZED_GAP_B_ROOT,
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
        materialized_dump_root = _materialized_root(date_str, materialized_gap_a_root, materialized_gap_b_root)
        d = materialized_dump_root / date_str
        if d.is_dir():
            shutil.rmtree(d)
            print(f"{date_str}: deleted materialized dump -> {d}", file=sys.stderr)
        return
    if date_str < LEGACY_CUTOVER:
        ym = date_str[:7]
        for root in (legacy_live_dump_root, legacy_archive_dump_root):
            for d in sorted(root.glob(f"{ym}-*")):
                if d.is_dir():
                    shutil.rmtree(d)
                    print(f"{date_str}: deleted raw dump -> {d}", file=sys.stderr)
    else:
        d = dump_root / date_str
        if d.is_dir():
            shutil.rmtree(d)
            print(f"{date_str}: deleted raw dump -> {d}", file=sys.stderr)


def _existing_snapshot_path(date_str: str, snapshot_dir: Path) -> Path | None:
    """A month's snapshot may exist as either tree-<date>.json.gz (current
    default) or the older, uncompressed tree-<date>.json (written before
    gzip-by-default landed) -- either counts as "already built" for resume
    purposes. Prefers the gzipped path if somehow both exist."""
    gz_path = snapshot_dir / f"tree-{date_str}.json.gz"
    if gz_path.exists():
        return gz_path
    plain_path = snapshot_dir / f"tree-{date_str}.json"
    if plain_path.exists():
        return plain_path
    return None


def ensure_snapshot(
    date_str: str,
    get_xml_path: Callable[[], Path],
    snapshot_dir: Path,
    workers: int | None,
    content_cache_dir: Path = DEFAULT_CONTENT_CACHE_DIR,
) -> Path:
    """get_xml_path is called (triggering ensure_month's fetch/decompress)
    only if a snapshot doesn't already exist and can't be reused from the
    live tree either -- so an already-completed month's raw dump, which
    cleanup_raw_dump deletes right after its snapshot is written, is never
    re-fetched on a resumed run just to be thrown away again.

    Writes both tree-<date>.json.gz (the assembled tree, what pipeline.compare
    diffs) and content-<date>.json.gz (see pipeline.content_cache -- the
    small cache of build_tree_json's inputs, letting a future
    build_tree_json-only fix skip re-parsing the dump and re-running
    compute_all_content_sizes). Both are gzipped by default -- see
    notes/richer-backfill-snapshots-plan.md -- since 153 months of either
    adds up at full size and neither is ever read partially."""
    existing = _existing_snapshot_path(date_str, snapshot_dir)
    if existing is not None:
        print(f"{date_str}: snapshot already built -> {existing}", file=sys.stderr)
        return existing

    snapshot_path = snapshot_dir / f"tree-{date_str}.json.gz"
    if date_str == _live_content_version() and LIVE_TREE_JSON.exists():
        print(f"{date_str}: matches live docs/data/tree.json's __content_version__, reusing it "
              f"instead of reprocessing", file=sys.stderr)
        tree = json.loads(LIVE_TREE_JSON.read_text(encoding="utf-8"))
        write_json_gz(snapshot_path, tree)
        return snapshot_path

    xml_path = get_xml_path()
    tree, content_cache = process_dump(xml_path, workers=workers)
    write_json_gz(snapshot_path, tree)
    print(f"{date_str}: wrote snapshot -> {snapshot_path}", file=sys.stderr)
    print(f"{date_str}: root stats: {tree['root']['stats']}", file=sys.stderr)

    content_cache_path = content_cache_dir / f"content-{date_str}.json.gz"
    write_content_cache(content_cache_path, content_cache)
    print(f"{date_str}: wrote content cache -> {content_cache_path}", file=sys.stderr)
    return snapshot_path


def rebuild_tree_from_cache(date_str: str, snapshot_dir: Path, content_cache_dir: Path) -> Path:
    """Rebuilds tree-<date>.json.gz from its cached content-<date>.json.gz,
    skipping parse_dump and compute_all_content_sizes entirely -- for
    propagating a build_tree_json/build_category_graph-level logic fix into
    already-backfilled months without a full slow rebuild. Overwrites
    whatever snapshot already exists (that's the point). Raises FileNotFoundError
    if no content cache exists for this month (never backfilled since the
    cache was introduced -- needs a full `ensure_snapshot` run instead)."""
    content_cache_path = content_cache_dir / f"content-{date_str}.json.gz"
    if not content_cache_path.exists():
        raise FileNotFoundError(
            f"no content cache for {date_str} at {content_cache_path} -- "
            f"run a full backfill for this month first"
        )
    cache = load_content_cache(content_cache_path)
    inputs = rebuild_inputs_from_cache(cache)
    refile_category(inputs.graph, "धर्मशास्त्रम्", new_parent_title="ग्रन्थाः", old_parent_title=inputs.graph.root_title)

    tree = build_tree_json(
        inputs.dump_index, inputs.graph, inputs.main_nodes,
        inputs.transclusion_map, inputs.content_index, inputs.reverse_transclusion_map,
    )
    snapshot_path = snapshot_dir / f"tree-{date_str}.json.gz"
    write_json_gz(snapshot_path, tree)
    print(f"{date_str}: rebuilt snapshot from content cache -> {snapshot_path}", file=sys.stderr)
    print(f"{date_str}: root stats: {tree['root']['stats']}", file=sys.stderr)
    return snapshot_path


def _main_rebuild_trees_only(args: argparse.Namespace) -> None:
    """--rebuild-trees-only entry point: rebuild tree-<date>.json.gz for each
    requested month from its cached content-<date>.json.gz (see
    rebuild_tree_from_cache), then recompute every consecutive pair's diff
    among those months and OVERWRITE (not skip) any existing changelog entry
    for that exact old/new date pair -- the whole point is picking up
    corrected stats for transitions already in the changelog. Months with no
    content cache are skipped with a warning (never backfilled since the
    cache was introduced)."""
    content_cache_dir: Path = args.content_cache_dir
    months = args.months if args.months is not None else sorted(
        p.name.removeprefix("content-").removesuffix(".json.gz")
        for p in content_cache_dir.glob("content-*.json.gz")
    )

    rebuilt_by_date: dict[str, Path] = {}
    for date_str in sorted(months):
        try:
            rebuilt_by_date[date_str] = rebuild_tree_from_cache(date_str, args.snapshot_dir, content_cache_dir)
        except FileNotFoundError as e:
            print(f"{date_str}: {e} -- skipping", file=sys.stderr)

    ordered_months = sorted(rebuilt_by_date)
    if args.changelog.exists():
        log = json.loads(args.changelog.read_text())
    else:
        log = []
    entries_by_pair = {(e.get("old_date"), e.get("date")): e for e in log}

    for old_date, new_date in zip(ordered_months, ordered_months[1:]):
        old_iso, new_iso = f"{old_date}T00:00:00Z", f"{new_date}T00:00:00Z"
        pair = (old_iso, new_iso)
        if pair not in entries_by_pair:
            # Not a transition the changelog tracks (e.g. these two months
            # aren't actually adjacent in the full backfilled sequence) --
            # rebuilding trees doesn't imply inventing new changelog entries.
            continue

        print(f"\n=== recomputing {old_date} -> {new_date} ===", file=sys.stderr)
        report = build_report(rebuilt_by_date[old_date], rebuilt_by_date[new_date])
        print_summary(report)

        existing_entry = entries_by_pair[pair]
        existing_entry.update(report)
        print(f"updated changelog entry #{existing_entry.get('id')}", file=sys.stderr)

    log.sort(key=lambda e: e["date"])
    args.changelog.parent.mkdir(parents=True, exist_ok=True)
    args.changelog.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
    print(f"\nwrote {args.changelog}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months", nargs="+", default=None,
                     help="months to backfill, oldest first, as YYYY-MM-01 (default: full available "
                          "range -- every Internet Archive month, every materialized gap month "
                          "(reconstructed on demand), plus the 3 current-era months, queried live)")
    ap.add_argument("--dump-root", type=Path, default=DEFAULT_DUMP_ROOT,
                     help="directory under which each current-era month gets its own <date>/ subdir")
    ap.add_argument("--legacy-live-dump-root", type=Path, default=DEFAULT_LEGACY_LIVE_DUMP_ROOT,
                     help="directory under which each legacy-era month served by the live rolling window "
                          "gets its own subdir")
    ap.add_argument("--legacy-archive-dump-root", type=Path, default=DEFAULT_LEGACY_ARCHIVE_DUMP_ROOT,
                     help="directory under which each legacy-era month served by Internet Archive "
                          "gets its own subdir")
    ap.add_argument("--materialized-gap-a-root", type=Path, default=DEFAULT_MATERIALIZED_GAP_A_ROOT,
                     help="directory where each MATERIALIZED_START/END date (the Internet-Archive/"
                          "live-window gap, 2022-06 through 2025-10) gets its own subdir, generated on "
                          "demand one month at a time and deleted again once its snapshot is written "
                          "(see _ensure_materialized_month)")
    ap.add_argument("--materialized-gap-b-root", type=Path, default=DEFAULT_MATERIALIZED_GAP_B_ROOT,
                     help="directory where each FOURTH_ERA_START/END date (the earlier pre-legacy-coverage "
                          "gap, 2012-01 through 2014-06) gets its own subdir, same on-demand treatment "
                          "as --materialized-gap-a-root")
    ap.add_argument("--materialize-src-dir", type=Path, default=DEFAULT_MATERIALIZE_SRC_DIR,
                     help="directory to cache the ~530MB sawikisource-latest-pages-meta-history.xml.bz2 "
                          "in, auto-downloaded once on first need and reused for every materialized month")
    ap.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR,
                     help="where to write per-month tree.json-shaped snapshots (gitignored, throwaway)")
    ap.add_argument("--content-cache-dir", type=Path, default=DEFAULT_CONTENT_CACHE_DIR,
                     help="where to write/read per-month content caches (see pipeline.content_cache) -- "
                          "gitignored, lets a build_tree_json-only fix skip re-parsing the dump and "
                          "re-running the slow content-size computation")
    ap.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG,
                     help="changelog.json to append pairwise diffs to")
    ap.add_argument("--workers", type=int, default=None, help="worker processes for content-size computation")
    ap.add_argument("--keep-raw-dumps", action="store_true",
                     help="don't delete each month's raw dump (.xml/.bz2) after its snapshot is written -- "
                          "by default, raw dumps are deleted immediately since the snapshot is all that's "
                          "ever needed afterward (see cleanup_raw_dump)")
    ap.add_argument("--rebuild-trees-only", action="store_true",
                     help="for each of --months (default: every month with an existing content cache), "
                          "rebuild tree-<date>.json.gz from its cached content-<date>.json.gz instead of "
                          "fetching/reparsing anything -- for propagating a build_tree_json/"
                          "build_category_graph-level logic fix into already-backfilled months. Then "
                          "re-diffs and rewrites docs/data/changelog.json from the corrected snapshots, "
                          "same as a normal run. Months with no cached content (backfilled before the "
                          "cache existed) are skipped with a warning, not erred on.")
    args = ap.parse_args()

    if args.rebuild_trees_only:
        _main_rebuild_trees_only(args)
        return

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
            d, args.dump_root, args.legacy_live_dump_root, args.legacy_archive_dump_root,
            args.materialized_gap_a_root, args.materialized_gap_b_root, args.materialize_src_dir,
        )
        try:
            snapshot_path = ensure_snapshot(date_str, get_xml_path, args.snapshot_dir, args.workers, args.content_cache_dir)
        except RootCategoryMissing as e:
            # Too early in sa.wikisource's history for this mirror's tree
            # model (see RootCategoryMissing) -- skip this month rather than
            # aborting the whole run; any comparison pairs involving it are
            # dropped below.
            print(f"{date_str}: {e} -- skipping this month", file=sys.stderr)
            if not args.keep_raw_dumps:
                cleanup_raw_dump(date_str, args.dump_root, args.legacy_live_dump_root, args.legacy_archive_dump_root,
                                  args.materialized_gap_a_root, args.materialized_gap_b_root)
            continue
        snapshots_by_date[date_str] = snapshot_path
        if not args.keep_raw_dumps:
            cleanup_raw_dump(date_str, args.dump_root, args.legacy_live_dump_root, args.legacy_archive_dump_root,
                              args.materialized_gap_a_root, args.materialized_gap_b_root)

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
