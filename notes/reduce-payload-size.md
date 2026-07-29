# Reduce tree.json / changelog.json payload size

`docs/data/tree.json` is ~28MB uncompressed, `docs/data/changelog.json` ~18MB. Both
gzip down to ~1.4-1.5MB each (confirmed via `gzip -c | wc -c`), so the fix is serving
them compressed with proper caching -- not restructuring the data.

Found while debugging why testing over an ngrok tunnel on a mobile plan burned
through a data cap fast: `make serve` (`python -m http.server`) sends no
`Content-Encoding: gzip` and no `Cache-Control` headers, so every load -- and every
reload, even unchanged -- re-transfers the full uncompressed ~46MB combined. Real
GH Pages hosting does serve pre-compressed + cached responses, so production
users are likely not hitting this anywhere near as hard, but it's unconfirmed
whether GH Pages' defaults are actually good enough here (e.g. does it compress
these specific files, what cache lifetime does it set) -- worth checking directly
against the live site's response headers before assuming this is only a local
`make serve` problem.

Two independent angles worth checking:
1. Confirm/measure actual GH Pages response headers for `tree.json`/`changelog.json`
   (`curl -sI --compressed` against the live URLs) -- is compression + caching
   already happening for real visitors, or does GH Pages need an explicit nudge
   (e.g. a `.nojekyll`-adjacent config, or pre-gzipping the files at commit time)?
2. For local dev (`make serve`), consider a small custom server (or a documented
   `python -m http.server` alternative) that at least sets `Cache-Control` so a
   normal (non-incognito) browser tab doesn't re-fetch on every reload during
   iteration. Lower priority than #1 since it only affects local testing cost, not
   real users.
