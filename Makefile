scrape:
	python scrape.py

check-dump:
	python -m pipeline.fetch --check-only

fetch-dump:
	python -m pipeline.fetch

fetch-dump-force:
	python -m pipeline.fetch --force

process:
	python -m pipeline.process --out docs2/data/tree2.json

serve:
	cd docs && python -m http.server

serve2:
	cd docs2 && python -m http.server 8001

ngrok:
	ngrok http 8001
