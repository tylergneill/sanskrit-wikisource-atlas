"""Refreshes docs/data/source_eras.json, the small sidecar file
docs/about.html's Snapshots section reads to describe the changelog's two
live-rolling-window source eras (mediawiki_content_current and the legacy
live rolling window) accurately.

Deliberately separate from pipeline.backfill: this does two live lookups
(current_era_months() and fetch_legacy.list_live_snapshots(), both against
dumps.wikimedia.org) that have nothing to do with
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

# Internet Archive's sa.wikisource coverage, pinned rather than queried.
#
# The pipeline does not use IA dumps at all (see notes/internet-archive-dumps.md
# and notes/interpretive-decisions.md section 6) and about.js no longer reads
# these fields; they survive only as the machine-readable counterpart to that
# note. Deriving them live cost dozens of HTTP requests -- one metadata lookup
# per archived snapshot -- to re-learn a set that cannot change: sa.wikisource's
# IA upload pipeline stalled after 2022-05-01, so this is a closed record, not a
# rolling window. Verified against a live listing on 2026-07-31 (76 snapshots).
ARCHIVE_START = "2011-09-01"
ARCHIVE_END = "2022-05-01"
ARCHIVE_GAP_RANGES = [
    ["2011-11-01", "2014-06-01"],
    ["2015-01-01", "2015-01-01"],
    ["2015-05-01", "2015-05-01"],
    ["2018-04-01", "2018-07-01"],
    ["2019-04-01", "2020-06-01"],
]


def _month_before(date_str: str) -> str:
    """YYYY-MM-01 one calendar month earlier."""
    year, month = int(date_str[:4]), int(date_str[5:7])
    month -= 1
    if month == 0:
        month, year = 12, year - 1
    return f"{year:04d}-{month:02d}-01"


def source_era_boundaries() -> dict[str, object]:
    """Queries both live-rolling-window sources (mediawiki_content_current
    via current_era_months, and the legacy live window via
    fetch_legacy.list_live_snapshots) for their current rolling-start months --
    docs/about.html's Snapshots section needs these two dates (the
    current-format and legacy-format live windows' own rolling starts) to
    describe the changelog's source coverage accurately, but neither is
    recoverable from changelog.json's entries themselves (no per-entry
    provenance is recorded there), and neither is a fixed date (both windows
    slide forward over time) -- so this is queried fresh on every call and
    written to a small sidecar file (DEFAULT_SOURCE_ERAS, see main()) rather
    than guessed client-side.

    Internet Archive coverage is emitted from pinned constants rather than
    queried (see ARCHIVE_START above for why), and materialized_ranges is a
    single contiguous span, since every month older than the legacy-live
    window is materialized."""
    era1_months = current_era_months()
    era1_start = min(era1_months) if era1_months else None

    # ONE HTTP request (a directory listing), vs the dozens
    # list_available_months() cost -- it issued a per-date metadata lookup for
    # all ~76 Internet Archive snapshots, and IA coverage is no longer used to
    # decide anything (see notes/internet-archive-dumps.md). All we need from
    # this source now is where its rolling window currently starts.
    live_dates = fetch_legacy.list_live_snapshots()
    era2_start = f"{live_dates[0][:4]}-{live_dates[0][4:6]}-01" if live_dates else None

    # Everything from MATERIALIZED_FLOOR up to the legacy-live window is
    # materialized -- one contiguous span. about.js derives the timeline's
    # materialized bar from era2_rolling_start directly and doesn't read this,
    # but it stays accurate so the JSON isn't quietly lying about what was
    # built.
    last_materialized = _month_before(era2_start) if era2_start else None
    materialized_ranges = (
        [[MATERIALIZED_FLOOR, last_materialized]] if last_materialized else []
    )

    return {
        "era1_rolling_start": era1_start,
        "era2_rolling_start": era2_start,
        "archive_start": ARCHIVE_START,
        "archive_end": ARCHIVE_END,
        "archive_gap_ranges": ARCHIVE_GAP_RANGES,
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
