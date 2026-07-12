# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A more accessible browsing interface for the Sanskrit text collection at sa.wikisource.org. Wikisource's category structure is hard to browse (no good overview for non-technical users, disorienting subcategory nesting, no metadata like filesize, no transliteration). This project scrapes the category tree via the MediaWiki API and renders it as a static, searchable, transliteration-aware site published to GitHub Pages from `docs/`.

## Architecture

Two independent halves connected by one generated JSON file:

1. **Scraper** (`scrape.py`) — a single-file Python script that walks the MediaWiki category API starting from `वर्गः:ग्रन्थाः` (and separately injects `वर्गः:धर्मशास्त्रम्` as a top-level sibling, since it isn't actually nested under ग्रन्थाः on the source site). It recursively builds a `CatSkeleton` tree (categories + pages, also descending into MediaWiki subpages — see below), fetching each page's byte-size/timestamp inline as it's discovered (no separate page-metadata pass), computes recursive size/count stats per category, and writes the result to `docs/data/tree.json`. This is the *only* input the frontend consumes.

   `build_skeleton()` traverses **depth-first**: for each subcategory it finds, it fully recurses into that subcategory (including all of *its* subcategories and page/subpage discovery) before moving on to the next sibling. This means the live category-count progress line's counter and current-activity text (see `ActivityStatus`/`activity` in `scrape.py`) can appear "stuck" deep in one branch (e.g. working through every work under a single niche subcategory) for a while before surfacing back up to a sibling at a shallower level — that's expected, not a hang. A genuine rate-limit stall is distinguishable from this: the status line shows `RATE-LIMITED: waiting Ns` and counts down live, rather than just showing the same category/activity text motionlessly.

2. **Frontend** (`docs/`) — a static, dependency-free vanilla JS app (`app.js`) that fetches `data/tree.json` client-side and renders a two-pane UI: an expandable/collapsible sidebar tree and a main content pane. No build step, no bundler, no framework — `docs/` is served as-is by GitHub Pages. The only external dependency is the Sanscript CDN script (loaded in `index.html`) used for on-the-fly Devanagari → IAST/ITRANS/HK/ISO/SLP1 transliteration, applied purely in the browser (source data is always stored in Devanagari).

Because the frontend has no build step, `docs/` (including `docs/data/tree.json`) is what's actually deployed — regenerating `tree.json` and committing it *is* the deploy step for content updates.

## Commands

Run the scraper (regenerates `docs/data/tree.json`):
```
make scrape
```
equivalent to:
```
python scrape.py
```

Useful scraper flags:
- `--out PATH` — output path (default `docs/data/tree.json`)
- `--delay SECONDS` — delay between category/page-listing API requests (default `0.5`; the scraper is deliberately polite/single-threaded against the live Wikisource API)
- `--subpage-delay SECONDS` — delay for the MediaWiki subpage-discovery loop specifically (defaults to `--delay` if unset); see "Wikisource API etiquette / rate limiting" below for why this is worth tuning separately. `0.2`–`0.3` has performed well in practice.
- `--debug` — log every API call (cache hit/miss, timing, retries) to stderr

There is no test suite, linter, or build step in this repo. To preview the frontend locally, serve `docs/` with any static file server (e.g. `python -m http.server`) from within that directory, since `app.js` fetches `./data/tree.json` via a relative path (won't work from `file://`).

## Disk-backed API response cache (`.api_cache/`)

`api_get()` persists every raw API response to `.api_cache/<sha256-of-params>.json` (gitignored) and checks disk before making a request. This means: reruns of the scraper (during dev iteration, or resuming after a rate-limit interruption) replay already-answered requests from disk instantly instead of re-hitting the live API, and only genuinely new requests (new categories, new pages, code changes that add new API calls) touch the network. Delete `.api_cache/` to force a fully fresh crawl. This cache holds **metadata only** — category listings, page sizes, timestamps — never article content/wikitext/HTML; the scraper has no code path that fetches page bodies at all (see "What this is").

## Wikisource API etiquette / rate limiting

Every request from `api_get()` sends `maxlag=5`, the standard MediaWiki bot-etiquette signal for non-interactive tasks (see [API:Etiquette](https://www.mediawiki.org/wiki/API:Etiquette)).

**The rate limiter and its actual numbers are documented** at https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits (dated 2026, "subject to experimentation and change"). It's Wikimedia's edge/CDN limiter (Envoy) — 429 responses carry `x-envoy-ratelimited: true` and a `Retry-After: <seconds>` header. `Retry-After` is **not a fixed backoff constant**; it's remaining time in Wikimedia's rolling per-minute window, which is why observed values vary a lot (26s, 34s, 38s, 39s, 45-95s across different trips). **`api_get()` reads and honors this header exactly**, rather than guessing a backoff duration; it only falls back to `delay_s * 2**attempt` if the header is absent or unparseable.

Limits are enforced **per client identity, per minute** — not a single global threshold:
- **Unidentified** (no identifying User-Agent info beyond IP): **10 req/min**.
- **User-Agent only** (unauthenticated, but with a compliant User-Agent — contact info/URL in the `Name/version (URL; contact)` format): **200 req/min**.
- Authenticated/bot-flagged accounts get higher still (2000/min or exempt), not applicable here since the scraper is unauthenticated.

**This fully explains what was previously an open mystery in this file: `scrape.py`'s `session.headers["User-Agent"]` had no contact info, which put it in the 10 req/min "Unidentified" bucket.** Confirmed live by burst-testing sa.wikisource.org directly: with the old User-Agent, requests tripped a 429 by request #11; after adding the repo URL to the User-Agent (see `scrape.py`'s `session` setup), 60/60 rapid-fire requests succeeded with zero 429s. This is the primary reason subpage descent (see below) previously tripped the rate limiter reliably on deeply-nested works — not fundamentally a pacing problem, a *client-identification* problem. Always keep the User-Agent's contact info current/valid; removing it would silently regress the crawl back to the 10 req/min tier.

At the 200 req/min tier, a steady-state pace faster than ~0.3s/request risks tripping the limiter again if sustained continuously; `--delay 0.2` has performed well in practice for the category-tree walk specifically (real request patterns have natural gaps, e.g. local processing between requests, that a raw synthetic burst test doesn't have). If exploring the live API by hand (e.g. `curl`), use the same compliant User-Agent and throttle yourself the same way the scraper does — a burst of bare `curl` calls with a generic/default User-Agent can still trip the 10 req/min bucket and affect a subsequent scraper run sharing the same source IP, since the limit is per-client-identity, and an unidentified curl call doesn't share the scraper's compliant identity.

`scrape.py` reports live activity (`ActivityStatus`/`activity`) so a rate-limit backoff is visibly distinguishable from a hang — see the "Architecture" section above.

## Key data shape (`docs/data/tree.json`)

```
{ "root": Node }

Node (category/collection):
  { id: "cat:<path>", type: "category"|"collection", title, children: [Node], pages: [Page], stats: { bytes, content_bytes_est, count, last_changed } }

Category-pointer (a second+ filing of a category already inlined elsewhere):
  { id: "cat:<path>", type: "category-pointer", title, points_to: "cat:<other id>", stats: { bytes, content_bytes_est, count, last_changed } }

Page:
  { id: "page:<title>", type: "page", title, url, stats: { bytes, content_bytes_est, last_changed } }
```
- `title` fields are raw Devanagari (the `वर्गः:` / `Category:` namespace prefix is stripped); the frontend transliterates on render, never the scraper.
- Category `id`s are **path-derived**, not title-only: `cat_id_for_path()` joins the full chain of ancestor titles from root down to that occurrence (e.g. `cat:उपनिषदः/प्रमुखोपनिषदः/कठोपनिषत्`). This is what makes two occurrences of the same category (see below) distinguishable by id.
- The scraper hardcodes an exclusion list of Wikisource maintenance/junk categories (e.g. `निष्कासनाय`, `अनिर्दिष्टानि पुटानि`) — if new junk categories appear in scraped output, add them to that list in `build_skeleton()` rather than filtering in the frontend.
- `stats.bytes` is raw MediaWiki wikitext size (`rvprop=size` on the latest revision) — **not a meaningful "how much content" number on its own**, since it's dominated by markup/templates/category-tag overhead on short pages and includes redirects/deletion-stubs/bare-transclusion pointers that carry zero real text. `stats.content_bytes_est` is the frontend-facing, meaningful number: `estimate_content_bytes()` in `scrape.py` subtracts an estimated overhead (`413.49 + 0.04558 × raw_bytes`, fit by linear regression against a live sample of 70 real content pages, R²=0.956; ~17% of an 84-page sample were non-content pages, which this formula does not specifically detect — it just naturally floors near-zero for tiny stub/redirect pages, which mostly is the right direction). This is a whole-corpus statistical estimate, not a real per-page measurement (that would require fetching and parsing every page's actual wikitext via `rvprop=content`, which the scraper deliberately does not do — see the disk-cache section above). The frontend (`app.js`/`about.js`) displays `content_bytes_est`, falling back to `bytes` only if it's absent (e.g. pre-existing data from before this field was added).

### Multi-parented categories (`category-pointer`)

Wikisource's category graph is not a strict tree: a category can legitimately be filed under more than one parent (confirmed 15 real cases as of the 2026-07 crawl — see `notes/multi_parent_categories_plan.md` for the full table and the दर्शन/उपनिषत्/रचनाः clusters they fall into). Neither occurrence is more "real" or "canonical" than the other — which one ends up holding the actual content in the JSON is purely an artifact of depth-first crawl order, not a meaningful distinction.

`build_skeleton()` tracks a `full_title -> id` map (`seen_cats`) across the whole crawl. The first time a category is reached, it gets a normal `category` node with real `children`/`pages`. Every subsequent time the *same* category is reached (via a different parent), it instead returns a `category-pointer` node: no `children`/`pages` of its own, just a `points_to` field naming the id of the occurrence that holds them. This keeps `tree.json` free of duplicated content — nothing downstream has to remember to dedupe a subtree by hand.

**Stats are still real on every occurrence, including pointers.** `attach_stats()` runs as a separate pass after the tree is built: for every node (including pointers, which resolve through `points_to`), it walks the full deduped set of distinct pages reachable from that node and sums bytes/count/max(last_changed) over that set — not a naive bottom-up sum of children's precomputed totals, which would double-count a shared category at whichever ancestor happens to contain both its occurrences. Concretely: if two occurrences of a shared category share a close common ancestor (e.g. both under `उपनिषदः`), the dedup happens right there and nothing above needs special handling; if they don't converge until root (e.g. one under `धर्मशास्त्रम्`, the other under `वेदाः > ब्राह्मणम्`), each ancestor along the way legitimately counts the shared category in full, and only root dedupes. This also fixed a pre-existing, unrelated bug: pages that are genuine members of more than one category were being counted twice in every ancestor's rollup (root count dropped from an inflated 4,786 to the true distinct-page count of 4,625 once this landed).

The frontend (`app.js`) builds an id→node index (`state.byId`) and a sibling-id map (`state.siblingIds`) at load time. Both occurrences of a shared category are independently selectable/expandable in the sidebar and show their own real stats (no "canonical vs. duplicate" treatment) — hovering either one highlights its sibling(s) elsewhere via a shared `data-shared-group` attribute, with a tooltip naming where the sibling(s) live even when not currently visible on screen. The main pane renders full content at every occurrence it encounters (never collapses one in favor of the other) and adds a "see also: <location>" link next to a shared category's stats, which jumps the main pane to the sibling occurrence when clicked.

### Category/page title collisions (`index_page`)

A category can also collide with a same-titled *page* rather than another category: e.g. category `वर्गः:किरातार्जुनीयम्` (18 सर्गः as direct categorymembers) and a page `किरातार्जुनीयम्` whose own MediaWiki subpages are those same 18 सर्गः — two independent discovery paths (categorymembers vs. subpage-title-prefix matching) landing on the same underlying content. `merge_index_pages()` (runs once at root, before `attach_stats`) removes the page from its parent's `pages` list and attaches it as `index_page` on the same-titled category node instead, so it renders once (category + a small "index page: ..." link) rather than as a second, fully-duplicated expandable block.

**Fixed 2026-07-12: the merge used to require the page's own subpages to already be a subset of the category's *other* direct page members, which does not hold in general** — confirmed live on कुमारसम्भवम्, where the category's only direct members were the index page itself plus one unrelated loose सर्ग, never the 6 सर्गः that were actually the index page's subpages. That subset check silently blocked the merge in this shape even though the title match was exact. Fixed by merging unconditionally on exact title match, and having `attach_stats()` flatten the index page's own subpages directly (`flatten_page`, with `setdefault` so a subpage title separately double-filed as a direct category member isn't double-counted) rather than assuming the category's `pages` already accounted for them.

**Known remaining gap, NOT fixed, out of scope for now:** this only matches on exact title equality. कुमारसम्भवम् is actually a *second*, harder problem layered on the same example: the category itself is titled with one legitimate Devanagari spelling (`कुमारसम्भवम्`, म् + भ conjunct) while its index page uses a different legitimate spelling (`कुमारसंभवम्`, ं anusvāra + भ) — genuinely different Unicode strings for the same work title, so `merge_index_pages()`'s title match never fires for this pair at all, and it still renders as a duplicate. Fixing this needs orthographic/Unicode-normalization-aware title matching (or a hardcoded alias table), which risks false-positive merges across genuinely different works with coincidentally similar spellings — deliberately not attempted here.

## Notes

- `docs/VERSION` holds a single `__version__ = "x.y.z"` line displayed in the UI header; bump it manually when making user-visible frontend changes.
- `notes/` holds prototype/spec material not yet absorbed into the maintained codebase. `notes/get_uncategorized.ipynb` is a working prototype for roadmap item 5 (uncategorized-pages bucket) — `list=querypage&qppage=Uncategorizedpages` plus an `list=allpages` + `prop=categories` sweep to find pages invisible to category-tree walking; port its logic into `scrape.py` when implementing that item, then remove the notebook.

## Implemented and ENABLED BY DEFAULT: scraper chasing MediaWiki subpages (the "conservatism problem")

**Status: implemented, enabled by default (no opt-out flag), and validated against बैबल् (Bible), the deeply-nested case that originally blocked this.** Subpage descent is unconditional in `build_skeleton()` — there is no `recurse_subpages` parameter or `--recurse-subpages`/`--no-recurse-subpages` flag anymore; every `make scrape` / `python scrape.py` run exercises this path. `docs/data/tree.json` reflects subpage-descended content as of the run that produced changelog entry #3 (`docs/data/changelog.json`), which isolates the resulting jump (+14,159 pages, +122.9% bytes) as newly-discovered real content, not a bug.

`build_skeleton()` originally discovered pages *only* via `list=categorymembers` recursion, with no logic to follow MediaWiki's `Title/Subtitle` subpage convention — so any work organized as an index/ToC page with real content living in `/`-delimited subpages was badly undercounted; the scraper saw only the shallow index page and none of its children, even though the index page itself was reached correctly through the category tree.

Concretely confirmed with नैषधीयचरितम् (Naiṣadhīyacarita), which exists on Wikisource in two organizational forms:
- One version is tagged directly into category `वर्गः:महाकाव्यम्` as 6 flat pages (e.g. `नैषधीयचरितम् सर्गाः १-५`), ~877KB total — this one the scraper always captured correctly, since categorymembers sees all 6.
- A second version is a single page titled `नैषधीयचरितम्‌` (note: has a trailing U+200C ZWNJ in its actual title — copy links carefully, a plain `नैषधीयचरितम्` lookup 404s) that is itself categorized into `महाकाव्यम्` but contains only a 2.1KB अनुक्रमणिका (table of contents) linking to 22 subpages named `नैषधीयचरितम्‌/प्रथमः सर्गः`, `नैषधीयचरितम्‌/द्वितीयः सर्गः`, ... `नैषधीयचरितम्‌/द्वाविंश: सर्गः`, each holding real verse text (~40-50KB apiece, comparable in total size to the first version). None of these 22 subpages were categorymembers of anything — they were invisible to the old scraper, which reported this whole second version as a single 2.1KB stray "page" with no real content.

`fetch_subpages()` queries `list=allpages&apprefix=<title>/&apnamespace=0` for a given page title, and `collect_subpages_recursive()` recursively follows discovered subpages to any nesting depth (e.g. `Title/Part1/Section2`), guarded by a `seen_titles` set. `build_skeleton()` calls this for every page found via `categorymembers` and flattens the results in as ordinary sibling pages in the same category node (not nested under the index page as a distinct node type — that's deferred to roadmap item #3, consolidation). Validated in isolation against नैषधीयचरितम् (22 सर्गः subpages, plus a misspelled duplicate, अष्ठमः सर्गः, all appeared correctly) and against बैबल् (313 subpages found; see below).

**What actually fixed the बैबल् rate-limiting failure — and it was not primarily about request pacing.** The scraper's `User-Agent` header (`session.headers` in `scrape.py`) previously had no contact info. Per Wikimedia's rate-limit docs (https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits, dated 2026), requests with no identifying User-Agent info are bucketed as "Unidentified": **10 requests/minute**. A request with a *compliant* User-Agent (contact info/URL in the `Name/version (URL; contact)` format) gets bucketed as "User-Agent only": **200 requests/minute** — a 20x difference. This was confirmed live by burst-testing sa.wikisource.org directly: 60/60 rapid requests succeeded with the fixed User-Agent, versus tripping a 429 by request #11 with the old one. The scraper's User-Agent now includes the repo URL. `Retry-After` values seen on a 429 (e.g. 26s, 34s, 38s, 49s across different trips) are not a fixed backoff constant on Wikimedia's side — they're remaining time in its rolling per-minute window, which is why they vary.

A `--subpage-delay` flag (defaults to `--delay` if unset) exists as a secondary, independently-tunable pacing knob for the subpage-discovery loop specifically, since it's unbatched (one `list=allpages` request per page probed, unlike the batched-up-to-50 page-metadata fetches) and is the dominant source of request volume when descending into deeply-nested works. `--subpage-delay 0.2`–`0.3` has performed well in practice against the real 200 req/min ceiling.

Since api_get() can now spend real time either fetching or sleeping out a rate-limit backoff, and both looked identical (silent) before, `scrape.py` also gained a live single-line activity status (`ActivityStatus`/`activity` in `scrape.py`) pinned below the scrolling category tree, showing whichever of "fetching:", "cached:", or "RATE-LIMITED: waiting Ns" is currently true, with the rate-limit wait counting down live rather than freezing silently. The status line is truncated to terminal width to avoid wrapping (a wrapped line can't be cleared correctly with a single carriage return).

**Cost-control caveat — do not remove the size gate.** Probing *every* page unconditionally for subpages roughly doubles total API request volume. Both known index pages are tiny (433 B, 2.1 KB) versus real content pages (tens to hundreds of KB), so `build_skeleton()` and `collect_subpages_recursive()` fetch each page's byte size first (batched, via `fetch_page_meta()`) and only probe for subpages if the page is under `SUBPAGE_PROBE_MAX_BYTES` (5,000 bytes). If a legitimate index/ToC page ever exceeds 5KB, it would be silently skipped — if discovered, raise the threshold rather than removing the gate entirely.

**There is no longer a separate page-metadata "phase 2."** Since subpage descent (now permanent) always pre-fetches every discovered page's metadata inline during the tree walk (`build_skeleton()`'s up-front `fetch_page_meta()` call), a standalone end-of-run metadata pass was always redundant once subpage descent covered the whole tree — removed, along with `--page-meta-delay` (nothing left to apply it to) and the `tqdm` progress-bar dependency it used. Verified via diff that output is byte-identical before/after this removal on the same cached data.

## Implemented: latest-change datestamps per item (roadmap item #4)

Roadmap item #4 ("compile overall latest-change datestamps for each item") is implemented and confirmed working. `fetch_page_meta()` fetches `rvprop=size|timestamp` (batched) per page; `build_skeleton()` rolls this up as `max()` over all descendant pages/subcategories into each node's `stats.last_changed`. Confirmed populated in generated `docs/data/tree.json` output (e.g. root `last_changed`).

This design (rollup over content pages, not read off any single page) is deliberate, motivated by the following finding: for a multi-part work fronted by a manually-created index/ToC page, does MediaWiki's own `timestamp` on that index page ever update when a linked child/subpage is edited? Answer: **no**. An index page's revision timestamp reflects edits made directly to that page (its link list, formatting, category tag) and nothing else — it is not a reliable freshness signal for the work as a whole. Any "last changed" stat for a work must be computed by the scraper as `max()` over the timestamps of all actual content pages/subpages, not read off the index page.

Confirmed via `prop=revisions&rvprop=timestamp` against both नैषधीयचरितम् organizational variants (see above):

**Case 1 — `नैषधीयचरितम् सम्पूर्णम्` (index, 433 B) and its 5 linked `सर्गाः` pages, all under category node नैषधीयचरितम्:**

| Page | Last changed | Size |
|---|---|---|
| नैषधीयचरितम् सम्पूर्णम् (index) | 2015-10-26T07:08:01Z | 433 B |
| नैषधीयचरितम् सर्गाः १-५ | 2024-10-10T07:38:28Z | 199,386 B |
| नैषधीयचरितम् सर्गाः ६-१० | 2015-10-26T07:26:50Z | 201,215 B |
| नैषधीयचरितम् सर्गाः ११-१५ | 2018-11-01T07:14:38Z | 180,089 B |
| नैषधीयचरितम् सर्गाः १६-२० | 2015-10-26T07:27:46Z | 211,707 B |
| नैषधीयचरितम् सर्गाः २०-२२ | 2015-10-26T07:37:09Z | 104,699 B |

The index's own timestamp (2015-10-26) predates the most recent real edit (सर्गाः १-५, 2024-10-10) by 9 years.

**Case 2 — `नैषधीयचरितम्‌` (index, has trailing U+200C ZWNJ in its title, 2,140 B) and its 22 linked `/सर्गः` subpages:**

| Page | Last changed | Size |
|---|---|---|
| नैषधीयचरितम्‌ (index) | 2018-03-01T14:29:02Z | 2,140 B |
| नैषधीयचरितम्‌/प्रथमः सर्गः | 2012-06-27T11:06:58Z | 49,318 B |
| नैषधीयचरितम्‌/द्वितीयः सर्गः | 2012-06-28T10:47:19Z | 32,921 B |
| नैषधीयचरितम्‌/तृतीयः सर्गः | 2012-06-27T11:22:44Z | 47,030 B |
| नैषधीयचरितम्‌/चतुर्थः सर्गः | 2012-06-27T11:22:58Z | 39,367 B |
| नैषधीयचरितम्‌/पञ्चमः सर्गः | 2012-06-27T11:09:08Z | 42,884 B |
| नैषधीयचरितम्‌/षष्ठः सर्गः | 2012-06-27T11:09:30Z | 37,734 B |
| नैषधीयचरितम्‌/सप्तमः सर्गः | 2012-06-28T10:46:41Z | 36,893 B |
| नैषधीयचरितम्‌/अष्टमः सर्गः | 2012-06-28T10:46:18Z | 36,233 B |
| नैषधीयचरितम्‌/अष्ठमः सर्गः (misspelled duplicate) | 2012-06-28T10:45:46Z | 113 B |
| नैषधीयचरितम्‌/नवमः सर्गः | 2012-06-27T11:12:27Z | 54,995 B |
| नैषधीयचरितम्‌/दशमः सर्गः | 2012-06-27T11:12:48Z | 46,539 B |
| नैषधीयचरितम्‌/एकादशः सर्गः | 2012-06-27T11:13:08Z | 50,327 B |
| नैषधीयचरितम्‌/द्वादशः सर्गः | 2012-06-27T11:15:33Z | 48,957 B |
| नैषधीयचरितम्‌/त्रयोदशः सर्गः | 2012-06-27T11:15:04Z | 21,632 B |
| नैषधीयचरितम्‌/चतुर्दशः सर्गः | 2012-06-27T11:16:15Z | 35,490 B |
| नैषधीयचरितम्‌/पञ्चदशः सर्गः | 2012-06-27T11:21:41Z | 36,171 B |
| नैषधीयचरितम्‌/षोडशः सर्गः | 2012-06-27T11:21:54Z | 45,875 B |
| नैषधीयचरितम्‌/सप्तदशः सर्गः | 2024-01-23T16:24:14Z | 58,438 B |
| नैषधीयचरितम्‌/अष्टादशः सर्गः | 2012-06-27T11:20:45Z | 48,221 B |
| नैषधीयचरितम्‌/एकोनविंश: सर्गः | 2012-06-27T11:20:57Z | 32,311 B |
| नैषधीयचरितम्‌/विंश: सर्गः | 2012-06-27T11:19:33Z | 42,508 B |
| नैषधीयचरितम्‌/एकविंश: सर्गः | 2012-06-27T11:19:49Z | 56,304 B |
| नैषधीयचरितम्‌/द्वाविंश: सर्गः | 2012-06-27T11:20:01Z | 54,924 B |

Here the index's own timestamp (2018-03-01) falls in the *middle* of the child edit range and still misses the outlier child (सप्तदशः सर्गः, edited 2024-01-23) by 6 years — confirming the index timestamp is just noise from unrelated edits to the index page itself (e.g. its category tag), not a rollup of anything.

Side finding, orthogonal to datestamping but relevant to roadmap item #3 (consolidation): `नैषधीयचरितम्‌/अष्ठमः सर्गः` is a 113-byte near-empty misspelled duplicate of `नैषधीयचरितम्‌/अष्टमः सर्गः` (36,233 B) — one more instance of the duplication problem described in [Wikisource data-quality notes], now confirmed at the subpage level too, not just at the top-level-work level.
