.PHONY: refresh-dump refresh-dump-force process serve ngrok backfill audit

# Resolve the latest complete monthly dump export on dumps.wikimedia.org and
# compare it against dump/: download/verify/decompress whatever's missing or
# stale, remove leftover files from a prior export, and no-op if everything
# already matches.
refresh-dump:
	python -m pipeline.fetch

# Same as refresh-dump, but re-download, re-verify, and re-decompress every
# part file even if already present and verified locally.
refresh-dump-force:
	python -m pipeline.fetch --force

# Build docs/data/tree.json from the downloaded dump.
process:
	python -m pipeline.process --out docs/data/tree.json

# Report likely breadcrumb/category structural problems on the live wiki for
# manual review -- never mutates the dump or docs/data/tree.json. See
# notes/wikisource-editing-plan.md.
audit:
	python -m pipeline.audit

# Walk backward through every available historical month (Internet Archive
# legacy dumps, the live rolling window, and the materialized gap neither
# covers -- 2022-06 through 2025-10, reconstructed on demand one month at a
# time via pipeline/materialize_snapshots.py, see pipeline/backfill.py's
# MATERIALIZED_MONTHS/_ensure_materialized_month), one pairwise comparison
# at a time, appending each to docs/data/changelog.json. Safe to interrupt
# and rerun -- already-fetched/materialized dumps, already-built snapshots,
# and already-logged changelog transitions are all skipped, not redone.
backfill:
	bash pipeline/run_backfill_sequence.sh --workers 10

# Serve the frontend (docs/) locally, on port 8000 (http.server's default).
serve:
	cd docs && python -m http.server

# Expose the local server (port 8000) via a public ngrok tunnel.
ngrok:
	ngrok http 8000
