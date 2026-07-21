#!/usr/bin/env bash
# One-time, out-of-band step that fills pipeline.backfill's MATERIALIZED_MONTHS
# gap (2022-06 through 2025-10 -- the span Internet Archive's stalled
# volunteer pipeline and the live rolling window both miss, see
# pipeline/backfill.py's module docstring): downloads the full-history dump,
# runs pipeline/materialize_snapshots.py against it, and fans each output
# file into the per-month dump/_materialized/<date>/ layout
# pipeline.backfill._materialized_xml_path expects. Safe to rerun -- the
# download is skipped if already present, and materialize_snapshots.py
# itself is only invoked for months not yet fanned out.
#
# After this completes, `make backfill` (pipeline/run_backfill_sequence.sh)
# picks up the newly materialized months automatically -- no separate step
# needed to "register" them.
#
# Usage: bash pipeline/materialize_gap.sh

set -euo pipefail
cd "$(dirname "$0")/.."

DUMP_URL="https://dumps.wikimedia.org/sawikisource/latest/sawikisource-latest-pages-meta-history.xml.bz2"
DUMP_DIR="dump/_materialize_src"
DUMP_BZ2="$DUMP_DIR/sawikisource-latest-pages-meta-history.xml.bz2"
STAGING_DIR="dump/_materialized/_staging"
MATERIALIZED_ROOT="dump/_materialized"

# Pulled from pipeline/backfill.py's MATERIALIZED_START/MATERIALIZED_END --
# kept in sync by hand since one is Python, the other bash; if that module
# ever changes these, update the two literals below to match.
START="2022-06"
END="2025-10"

mkdir -p "$DUMP_DIR" "$STAGING_DIR"

if [[ -f "$DUMP_BZ2" ]]; then
  echo "already downloaded -> $DUMP_BZ2"
else
  echo "downloading $DUMP_URL (~530MB)..."
  curl -L -C - --fail -o "$DUMP_BZ2" "$DUMP_URL" \
    -H "User-Agent: sanskrit-wikisource-mirror/2.0 (https://github.com/tylergneill/sanskrit-wikisource-mirror; polite; research use)"
fi

# Skip months that are already fanned out into dump/_materialized/<date>/
# from a prior run of this script.
MISSING_MONTHS=()
y="${START%-*}"; m="${START#*-}"
ey="${END%-*}"; em="${END#*-}"
while [[ "$y$m" -le "$ey$em" ]]; do
  d="$y-$m-01"
  day8="${y}${m}01"
  if [[ -f "$MATERIALIZED_ROOT/$d/sawikisource-${day8}-pages-articles.synth.xml" ]] \
     || [[ -f "$MATERIALIZED_ROOT/$d/sawikisource-${day8}-pages-articles.synth.xml.bz2" ]]; then
    : # already fanned out, skip
  else
    MISSING_MONTHS+=("$d")
  fi
  if [[ "$m" == "12" ]]; then y=$((y + 1)); m="01"; else m=$(printf "%02d" $((10#$m + 1))); fi
done

if [[ ${#MISSING_MONTHS[@]} -eq 0 ]]; then
  echo "all months in $START..$END already materialized under $MATERIALIZED_ROOT/ -- nothing to do"
  exit 0
fi

echo "materializing ${#MISSING_MONTHS[@]} month(s): ${MISSING_MONTHS[*]}"
python3 pipeline/materialize_snapshots.py "$DUMP_BZ2" \
  --start "$START" --end "$END" --outdir "$STAGING_DIR" --compress

for d in "${MISSING_MONTHS[@]}"; do
  day8="${d//-/}"
  src="$STAGING_DIR/sawikisource-${day8}-pages-articles.synth.xml.bz2"
  if [[ ! -f "$src" ]]; then
    echo "warning: expected $src not produced by materialize_snapshots.py -- skipping $d" >&2
    continue
  fi
  dest_dir="$MATERIALIZED_ROOT/$d"
  mkdir -p "$dest_dir"
  mv "$src" "$dest_dir/"
  echo "$d -> $dest_dir/$(basename "$src")"
done

rmdir "$STAGING_DIR" 2>/dev/null || true

echo "done. run \`make backfill\` to weave these into docs2/data/changelog2.json."
