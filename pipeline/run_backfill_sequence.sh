#!/usr/bin/env bash
# Runs pipeline.backfill one month-pair at a time, starting from the newest
# current-era month available right now (queried live via
# pipeline.backfill.current_era_months -- NOT a hardcoded list, since the
# live 3-month rolling window shifts forward over time and a hardcoded
# anchor would silently stop advancing once a newer month appears) and
# working backward through every older month, all of which are materialized
# (Internet Archive dumps are deliberately unused -- see
# notes/internet-archive-dumps.md). The month list comes from
# pipeline.backfill.default_months(), a plain calendar enumeration back to
# MATERIALIZED_FLOOR, so coverage is gap-free by construction. Each month is
# reconstructed on demand, one at a time, the
# moment its step runs (see pipeline.backfill._ensure_materialized_month) --
# nothing needs to be pre-generated before running this script. Each step is
# a separate `python -m pipeline.backfill --months OLDER NEWER` invocation,
# so progress/output is visible per-step and a failure on one month doesn't
# lose earlier progress. Already-fetched/materialized dumps and already-built
# snapshots are skipped/reused on rerun (see ensure_month/ensure_snapshot in
# pipeline/backfill.py). docs/data/changelog.json itself is deleted at the
# start of every run and rebuilt from scratch -- cheap, since every entry is
# just a diff of two already-cached snapshots -- so a rerun always reflects
# the current tree-assembly logic, never a stale entry from a prior schema
# or a since-fixed bug.
#
# This script ONLY walks backward, never forward -- per standing project
# convention, backfill must never process an older month before a newer
# one, at any granularity (see memory: feedback-backfill-newest-first). It
# does not attempt to "catch up" a changelog that has fallen behind the
# live current-era window; that's a separate, deliberate operation the user
# runs explicitly (a plain `python -m pipeline.backfill --months OLDER
# NEWER` for whichever specific newer months are needed), not something
# this script does automatically as a side effect.
#
# The walk is floored at pipeline.backfill.MATERIALIZED_FLOOR (2012-02, the
# first month whose cutoff lands after वर्गसर्वस्वम् was created on
# 2012-01-20). Earlier months can only ever raise RootCategoryMissing -- the
# root category this Atlas's tree model depends on does not exist yet -- so
# the floor avoids paying a full materialize + parse to rediscover that. The
# floor is applied by default_months() itself, so this script inherits it
# rather than reimplementing it. main() still catches RootCategoryMissing
# (see its docstring) as the backstop for anything unexpected at or above
# the floor.
#
# Usage: bash pipeline/run_backfill_sequence.sh [--workers N]

set -euo pipefail
cd "$(dirname "$0")/.."

# Always start this walk from a clean changelog -- every transition gets
# recomputed from its (reused-if-present) snapshots below regardless, so
# there's nothing a stale changelog.json preserves that this run wouldn't
# reproduce anyway, and deleting it up front guarantees no leftover entry
# from a prior run/schema survives unrecomputed.
rm -f docs/data/changelog.json

WORKERS_ARGS=()
if [[ "${1:-}" == "--workers" ]]; then
  WORKERS_ARGS=(--workers "$2")
fi

# Newest current-era month available right now (queried live -- see
# pipeline.backfill.current_era_months) -- the backward walk starts here.
echo "checking newest available current-era month..."
NEWEST_ANCHORED=$(python3 -c "
from pipeline.backfill import current_era_months
months = current_era_months()
print(months[-1])
")
echo "newest current-era month: ${NEWEST_ANCHORED}"

# Every backfillable month, oldest first, from pipeline.backfill's own
# default_months() -- a plain calendar enumeration from MATERIALIZED_FLOOR up
# to the current era, with no network query and no gaps by construction.
#
# This used to merge pipeline.fetch_legacy --list (Internet Archive + the
# legacy live rolling window) with the detected materialized holes. That is
# gone: IA dumps are deliberately unused (see notes/internet-archive-dumps.md
# and notes/interpretive-decisions.md section 6), so deriving the walk from IA
# availability would now silently skip every month IA never covered.
#
# stderr is NOT suppressed: if this fails (missing dependency, wrong python3
# on PATH), that must be visible rather than silently yielding an empty MONTHS
# and a script that looks like it did nothing.
echo "listing backfillable months..."
MONTHS=$(python3 -c "
from pipeline.backfill import default_months
for d in default_months():
    print(d)
" | awk -v cutover="$NEWEST_ANCHORED" '$0 < cutover')
if [[ -z "$MONTHS" ]]; then
  echo "error: default_months() returned no months -- aborting" >&2
  exit 1
fi

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
  # ${WORKERS_ARGS[@]+"${WORKERS_ARGS[@]}"} rather than a bare
  # "${WORKERS_ARGS[@]}": under `set -u`, expanding an EMPTY array is an
  # unbound-variable error on bash < 4.4 (macOS ships 3.2), which aborted the
  # whole run on step 1 whenever --workers was omitted -- after the changelog
  # had already been deleted.
  python3 -m pipeline.backfill --months "${OLDER}" "${NEWER}" ${WORKERS_ARGS[@]+"${WORKERS_ARGS[@]}"}
  echo
done

# pipeline.update_source_eras (docs/data/source_eras.json, read by
# docs/about.html's Snapshots section) does two live network lookups
# against dumps.wikimedia.org/Internet Archive -- this used to run
# unconditionally inside pipeline.backfill at the end of every step (~150+
# times over a full sequence) even though neither rolling window's start
# moves mid-sequence; it's a standalone concern now, run once here instead,
# after the whole walk finishes.
echo "updating source era boundaries..."
python3 -m pipeline.update_source_eras

echo "done."
