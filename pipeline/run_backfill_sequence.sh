#!/usr/bin/env bash
# Runs pipeline.backfill one month-pair at a time, starting from the newest
# current-era month already anchored in docs/data/changelog.json
# (2026-05-01) and working backward through every available legacy month
# (pipeline.fetch_legacy -- merged live rolling window + Internet Archive,
# see that module's docstring) down to the oldest (2011-09-01, as of this
# writing), splicing in every materialized month (the Internet-Archive/
# live-window gap, 2022-06 through 2025-10 -- see pipeline.backfill's
# MATERIALIZED_MONTHS). Each is reconstructed on demand, one at a time, the
# moment its step runs (see pipeline.backfill._ensure_materialized_month) --
# nothing needs to be pre-generated before running this script. Each step is
# a separate `python -m pipeline.backfill --months OLDER NEWER` invocation,
# so progress/output is visible per-step and a failure on one month doesn't
# lose earlier progress (already-appended changelog entries and
# already-fetched/materialized dumps are skipped/reused on rerun -- see
# ensure_month/ensure_snapshot in pipeline/backfill.py).
#
# Usage: bash pipeline/run_backfill_sequence.sh [--workers N]

set -euo pipefail
cd "$(dirname "$0")/.."

WORKERS_ARGS=()
if [[ "${1:-}" == "--workers" ]]; then
  WORKERS_ARGS=(--workers "$2")
fi

# Oldest current-era month already in the changelog -- the backward walk
# starts by connecting the newest legacy month to this one. (The legacy
# listing below now actually reaches up through 2026-07 itself, since the
# live rolling window overlaps the current-era window -- but months from
# here forward are deliberately still fetched via the current-era path in
# pipeline.backfill, see LEGACY_CUTOVER, so this script never walks past it.)
NEWEST_ANCHORED="2026-05-01"

# Legacy months, oldest first (merged live rolling window + Internet
# Archive, as pipeline.fetch_legacy reports them right now, filtered to
# before NEWEST_ANCHORED) -- reverse this list to walk backward.
# stderr is NOT suppressed here: if pipeline.fetch_legacy --list fails
# (network error, missing `requests` dependency, wrong python3 on PATH,
# etc.), that error must be visible rather than silently yielding an empty
# MONTHS and a script that looks like it did nothing.
echo "querying available legacy months..."
LEGACY_MONTHS=$(python3 -m pipeline.fetch_legacy --list | cut -f1 | sed 's/$/-01/' | awk -v cutover="$NEWEST_ANCHORED" '$0 < cutover')
if [[ -z "$LEGACY_MONTHS" ]]; then
  echo "error: pipeline.fetch_legacy --list returned no months -- aborting" >&2
  exit 1
fi

# Materialized months (the Internet-Archive/live-window gap, see
# pipeline.backfill's MATERIALIZED_MONTHS) -- all of them, unconditionally;
# each is reconstructed on demand by ensure_month/_ensure_materialized_month
# when its step actually runs, so there's no disk-existence check to apply
# here anymore.
echo "listing materialized months..."
MATERIALIZED_MONTHS=$(python3 -c "
from pipeline.backfill import MATERIALIZED_MONTHS
for d in MATERIALIZED_MONTHS:
    print(d)
" 2>/dev/null)

MONTHS=$(printf '%s\n%s\n' "$LEGACY_MONTHS" "$MATERIALIZED_MONTHS" | grep -v '^$' | sort -u)

# Build the full oldest-to-newest sequence, then reverse it in bash.
SEQUENCE=()
while IFS= read -r m; do
  SEQUENCE+=("$m")
done <<< "$MONTHS"
SEQUENCE+=("$NEWEST_ANCHORED")

REVERSED=()
for ((i=${#SEQUENCE[@]}-1; i>=0; i--)); do
  REVERSED+=("${SEQUENCE[$i]}")
done

echo "Walking backward through ${#REVERSED[@]} months, newest first:" "${REVERSED[@]}"
echo

for ((i=0; i<${#REVERSED[@]}-1; i++)); do
  NEWER="${REVERSED[$i]}"
  OLDER="${REVERSED[$((i+1))]}"
  echo "=================================================================="
  echo "=== step $((i+1))/$((${#REVERSED[@]}-1)): ${OLDER} -> ${NEWER} ==="
  echo "=================================================================="
  python3 -m pipeline.backfill --months "${OLDER}" "${NEWER}" "${WORKERS_ARGS[@]}"
  echo
done

echo "done."
