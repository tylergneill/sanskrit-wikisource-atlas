.PHONY: scrape refresh-dump refresh-dump-force process serve serve2 ngrok materialize-gap backfill

# Regenerate docs/data/tree.json (v1 frontend) via the live MediaWiki API.
scrape:
	python scrape.py

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

# Build docs2/data/tree2.json (v2 frontend) from the downloaded dump.
process:
	python -m pipeline.process --out docs2/data/tree2.json

# One-time, out-of-band step that fills the gap neither Internet Archive nor
# the live rolling window covers (2022-06 through 2025-10, see
# pipeline/backfill.py's MATERIALIZED_MONTHS): downloads sawikisource's
# full-history dump, reconstructs each missing month's state from it via
# pipeline/materialize_snapshots.py, and fans the output into
# dump/_materialized/<date>/. Run this before `make backfill` the first time
# (or whenever you want to fill in more of the gap) -- backfill only reads
# whatever's already materialized here, it never runs this step itself.
# Safe to rerun -- the ~530MB download and each month's reconstruction are
# both skipped if already done.
materialize-gap:
	bash pipeline/materialize_gap.sh

# Walk backward through every available historical month (Internet Archive
# legacy dumps, the live rolling window, and any gap months already filled
# in by `make materialize-gap`), one pairwise comparison at a time,
# appending each to docs2/data/changelog2.json. Safe to interrupt and
# rerun -- already-fetched dumps, already-built snapshots, and already-logged
# changelog transitions are all skipped, not redone.
backfill:
	bash pipeline/run_backfill_sequence.sh --workers 10

# Serve the v1 frontend (docs/) locally.
serve:
	cd docs && python -m http.server

# Serve the v2 frontend (docs2/) locally, on port 8001.
serve2:
	cd docs2 && python -m http.server 8001

# Expose the v2 local server (port 8001) via a public ngrok tunnel.
ngrok:
	ngrok http 8001
