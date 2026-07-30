#!/usr/bin/env bash
# Runs pipeline.backfill one month-pair at a time, starting from the newest
# current-era month available right now (queried live via
# pipeline.backfill.current_era_months -- NOT a hardcoded list, since the
# live 3-month rolling window shifts forward over time and a hardcoded
# anchor would silently stop advancing once a newer month appears) and
# working backward through every available legacy month (pipeline.fetch_legacy
# -- merged live rolling window + Internet Archive, see that module's
# docstring), splicing in every materialized month (every interior hole in
# the two legacy sources' combined coverage, detected live rather than
# hardcoded -- see pipeline.backfill.compute_materialized_months and
# MATERIALIZED_MONTHS). Each is reconstructed on demand, one at a time, the
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
# 2012-01-20). Internet Archive really does serve older dumps (2011-09,
# 2011-10), and they're real files this script would otherwise pass as
# explicit --months -- bypassing default_months()'s own floor and paying a
# full download + parse per run for months that can only ever raise
# RootCategoryMissing. main() still catches that exception (see its
# docstring) as the backstop for anything unexpected at or above the floor;
# the floor here just avoids the known-futile work rather than rediscovering
# it from the network every time.
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

# Legacy months, oldest first (merged live rolling window + Internet
# Archive, as pipeline.fetch_legacy reports them right now, filtered to
# before NEWEST_ANCHORED) -- reverse this list to walk backward.
# stderr is NOT suppressed here: if pipeline.fetch_legacy --list fails
# (network error, missing `requests` dependency, wrong python3 on PATH,
# etc.), that error must be visible rather than silently yielding an empty
# MONTHS and a script that looks like it did nothing.
echo "querying available legacy months..."
FLOOR=$(python3 -c "
from pipeline.backfill import MATERIALIZED_FLOOR
print(MATERIALIZED_FLOOR)
")
LEGACY_MONTHS=$(python3 -m pipeline.fetch_legacy --list | cut -f1 | sed 's/$/-01/' | awk -v cutover="$NEWEST_ANCHORED" -v floor="$FLOOR" '$0 >= floor && $0 < cutover')
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
