scrape:
	python scrape.py

serve:
	cd docs && python -m http.server

fetch-dump:
	python -m pipeline.fetch

fetch-dump-force:
	python -m pipeline.fetch --force

check-dump:
	python -m pipeline.fetch --check-only