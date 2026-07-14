.PHONY: scrape refresh-dump refresh-dump-force process serve serve2 ngrok

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

# Serve the v1 frontend (docs/) locally.
serve:
	cd docs && python -m http.server

# Serve the v2 frontend (docs2/) locally, on port 8001.
serve2:
	cd docs2 && python -m http.server 8001

# Expose the v2 local server (port 8001) via a public ngrok tunnel.
ngrok:
	ngrok http 8001
