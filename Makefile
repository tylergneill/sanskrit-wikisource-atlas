scrape:
	python scrape.py

serve:
	cd docs && python -m http.server

serve2:
	cd docs2 && python -m http.server 8001

fetch-dump:
	python -m pipeline.fetch

fetch-dump-force:
	python -m pipeline.fetch --force

check-dump:
	python -m pipeline.fetch --check-only

process:
	python -m pipeline.process --out docs2/data/tree2.json