.PHONY: refresh-dump refresh-dump-force process serve ngrok backfill rebuild-trees audit audit-update-about

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

# Build docs/data/tree.json from the downloaded dump. Override worker count
# with e.g. `make process WORKERS=4` (default: os.cpu_count()).
process:
	python -m pipeline.process --out docs/data/tree.json $(if $(WORKERS),--workers $(WORKERS))

# Report likely breadcrumb/category structural problems on the live wiki for
# manual review -- never mutates the dump or docs/data/tree.json. See
# notes/wikisource-editing-plan.md.
audit:
	python -m pipeline.audit

# Same as audit, but also regenerates the audit findings section of
# docs/about.html.
audit-update-about:
	python -m pipeline.audit --update-about

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

# Re-run build_tree_json against every month's already-cached content
# (dump/_backfill_content_cache/content-<date>.json.gz) instead of
# re-fetching/re-parsing dumps or re-running content-size computation --
# for propagating a tree-assembly-logic fix (build_tree_json/
# build_category_graph) into already-backfilled months cheaply. Also
# re-diffs and overwrites the affected docs/data/changelog.json entries in
# place. Months with no content cache yet (never backfilled since the cache
# was introduced) are skipped with a warning, not erred on. Override which
# months with e.g. `make rebuild-trees MONTHS="2022-06-01 2022-07-01"`
# (default: every month with an existing content cache).
rebuild-trees:
	python -m pipeline.backfill --rebuild-trees-only $(if $(MONTHS),--months $(MONTHS))

# Serve the frontend (docs/) locally, on port 8000 (http.server's default).
serve:
	cd docs && python -m http.server

# Expose the local server (port 8000) via a public ngrok tunnel.
ngrok:
	ngrok http 8000
