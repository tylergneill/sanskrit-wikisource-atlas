"""Refreshes docs/data/source_eras.json, the small sidecar file
docs/about.html's Snapshots section reads to describe the changelog's two
live-rolling-window source eras (mediawiki_content_current and the legacy
live rolling window) accurately.

Deliberately separate from pipeline.backfill: this does two live network
lookups (current_era_months() against dumps.wikimedia.org, and
fetch_legacy.list_available_months() against dumps.wikimedia.org + Internet
Archive) that take about a minute combined, but have nothing to do with
backfilling any particular month-pair -- both rolling windows' start dates
move independently of which months pipeline.backfill happens to be
processing right now. Previously this ran unconditionally at the end of
EVERY pipeline.backfill invocation, including each of the 150+ per-step
calls pipeline/run_backfill_sequence.sh makes over a full sequence, adding
that same ~1 minute per step for no reason (neither rolling window's start
moves mid-sequence). Now run once, standalone, after a sequence finishes
(see run_backfill_sequence.sh's final step) or whenever else a refresh is
wanted.

Usage:
    python -m pipeline.update_source_eras
    python -m pipeline.update_source_eras --source-eras /tmp/source_eras.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline import fetch_legacy
from pipeline.backfill import MATERIALIZED_FLOOR, current_era_months

DEFAULT_SOURCE_ERAS = Path(__file__).resolve().parent.parent / "docs" / "data" / "source_eras.json"


def _months_to_ranges(months: list[str]) -> list[list[str]]:
    """Compresses a sorted list of YYYY-MM-01 strings into contiguous
    [start, end] ranges (single months become [x, x]) -- so callers describing
    coverage/gaps don't need to enumerate every individual month."""
    if not months:
        return []
    ranges: list[list[str]] = []
    start = prev = months[0]
    for m in months[1:]:
        py, pm = (int(p) for p in prev.split("-")[:2])
        cy, cm = (int(p) for p in m.split("-")[:2])
        py, pm = (py, pm + 1) if pm < 12 else (py + 1, 1)
        if (cy, cm) == (py, pm):
            prev = m
            continue
        ranges.append([start, prev])
        start = prev = m
    ranges.append([start, prev])
    return ranges


def _month_before(date_str: str) -> str:
    """YYYY-MM-01 one calendar month earlier."""
    year, month = int(date_str[:4]), int(date_str[5:7])
    month -= 1
    if month == 0:
        month, year = 12, year - 1
    return f"{year:04d}-{month:02d}-01"


def _interior_gaps(months: list[str]) -> list[str]:
    """Given a sorted list of YYYY-MM-01 strings, returns every YYYY-MM-01
    month strictly between the first and last that's missing from the list --
    i.e. interior holes only, not "before the first" or "after the last"
    (those are just the edges of the range, not gaps within it)."""
    if len(months) < 2:
        return []
    present = set(months)
    gaps = []
    y, m = (int(p) for p in months[0].split("-")[:2])
    end_y, end_m = (int(p) for p in months[-1].split("-")[:2])
    while (y, m) < (end_y, end_m):
        date_str = f"{y:04d}-{m:02d}-01"
        if date_str not in present:
            gaps.append(date_str)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return gaps


def source_era_boundaries() -> dict[str, object]:
    """Queries both live-rolling-window sources (mediawiki_content_current
    via current_era_months, and fetch_legacy's live-window branch via
    list_available_months) for their current rolling-start months --
    docs/about.html's Snapshots section needs these two dates (the
    current-format and legacy-format live windows' own rolling starts) to
    describe the changelog's source coverage accurately, but neither is
    recoverable from changelog.json's entries themselves (no per-entry
    provenance is recorded there), and neither is a fixed date (both windows
    slide forward over time) -- so this is queried fresh on every call and
    written to a small sidecar file (DEFAULT_SOURCE_ERAS, see main()) rather
    than guessed client-side.

    Also records the Internet Archive's own coverage range and its interior
    gaps (archive_start/archive_end/archive_gap_ranges), computed from the
    same list_available_months() result with no extra network call. **The
    pipeline does not use IA dumps** -- see notes/internet-archive-dumps.md
    and notes/interpretive-decisions.md section 6 -- and about.js no longer
    reads these three fields. They are kept as the machine-readable record
    backing that note, so its prose stays checkable against reality.

    materialized_ranges is now a single contiguous span (MATERIALIZED_FLOOR
    through the month before era2_rolling_start), because every historical
    month is materialized rather than only the holes IA left behind. It used
    to be the set of detected gaps."""
    era1_months = current_era_months()
    era1_start = min(era1_months) if era1_months else None

    by_month = fetch_legacy.list_available_months()
    live_months = sorted(f"{ym}-01" for ym, dump in by_month.items() if dump.source == "live")
    era2_start = live_months[0] if live_months else None

    # Internet Archive coverage is recorded but NOT used by the pipeline (see
    # notes/internet-archive-dumps.md and notes/interpretive-decisions.md
    # section 6 -- every historical month is materialized instead). These
    # three fields are no longer read by about.js's timeline; they are kept as
    # the machine-readable record of what IA actually holds, which is exactly
    # what notes/internet-archive-dumps.md documents in prose. Refreshing them
    # keeps that note checkable against reality.
    archive_months = sorted(f"{ym}-01" for ym, dump in by_month.items() if dump.source == "archive")
    archive_start = archive_months[0] if archive_months else None
    archive_end = archive_months[-1] if archive_months else None
    archive_gap_ranges = _months_to_ranges(_interior_gaps(archive_months))

    # Everything from MATERIALIZED_FLOOR up to the legacy-live window is
    # materialized now -- one contiguous span, not the old set of detected
    # holes in IA's coverage. about.js derives the timeline's materialized bar
    # from era2_rolling_start directly and doesn't read this, but it stays
    # accurate so the JSON isn't quietly lying about what was built.
    last_materialized = _month_before(era2_start) if era2_start else None
    materialized_ranges = (
        [[MATERIALIZED_FLOOR, last_materialized]] if last_materialized else []
    )

    return {
        "era1_rolling_start": era1_start,
        "era2_rolling_start": era2_start,
        "archive_start": archive_start,
        "archive_end": archive_end,
        "archive_gap_ranges": archive_gap_ranges,
        "materialized_ranges": materialized_ranges,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-eras", type=Path, default=DEFAULT_SOURCE_ERAS,
                     help="sidecar JSON to write era 1/2's live rolling-start dates to, for "
                          "docs/about.html's Snapshots section")
    args = ap.parse_args()

    boundaries = source_era_boundaries()
    args.source_eras.parent.mkdir(parents=True, exist_ok=True)
    args.source_eras.write_text(json.dumps(boundaries, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote source era boundaries -> {args.source_eras}: {boundaries}", file=sys.stderr)


if __name__ == "__main__":
    main()
