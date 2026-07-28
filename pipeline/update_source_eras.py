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
from pipeline.backfill import current_era_months

DEFAULT_SOURCE_ERAS = Path(__file__).resolve().parent.parent / "docs" / "data" / "source_eras.json"


def source_era_boundaries() -> dict[str, str]:
    """Queries both live-rolling-window sources (mediawiki_content_current
    via current_era_months, and fetch_legacy's live-window branch via
    list_available_months) for their current rolling-start months --
    docs/about.html's Snapshots section needs these two dates (era 1's and
    era 2's own rolling starts) to describe the changelog's source eras
    accurately, but neither is recoverable from changelog.json's entries
    themselves (no per-entry provenance is recorded there), and neither is a
    fixed date (both windows slide forward over time) -- so this is queried
    fresh on every call and written to a small sidecar file
    (DEFAULT_SOURCE_ERAS, see main()) rather than guessed client-side."""
    era1_months = current_era_months()
    era1_start = min(era1_months) if era1_months else None

    by_month = fetch_legacy.list_available_months()
    live_months = [f"{ym}-01" for ym, dump in by_month.items() if dump.source == "live"]
    era2_start = min(live_months) if live_months else None

    return {"era1_rolling_start": era1_start, "era2_rolling_start": era2_start}


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
