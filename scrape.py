import argparse
import hashlib
import json
import logging
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
from skrutable.transliteration import Transliterator

BASE_URL = "https://sa.wikisource.org"
API_URL = f"{BASE_URL}/w/api.php"

log = logging.getLogger("scrape")

GRANTHAH_CAT = "वर्गः:ग्रन्थाः"
DHARMASHASTRA_CAT = "वर्गः:धर्मशास्त्रम्"

DEFAULT_OUT = "docs/data/tree.json"

REQUEST_DELAY_S = 0.5
TIMEOUT_S = 30
MAX_RETRIES = 3

# Only recurse into a discovered subpage's own subpages (grandchildren etc.)
# if that subpage's size is below this threshold. Real content pages (verse
# text etc.) run tens to hundreds of KB; only small index/ToC pages point to
# further subpages in practice (both known cases for नैषधीयचरितम् are well
# under 5KB: 433B and 2.1KB). Without this filter, recursively probing every
# real content page for further subpages roughly doubles total request volume
# and reliably trips Wikimedia's edge rate limiter.
#
# NOT applied to the top-level probe of a direct categorymember (see
# build_skeleton) -- that page is already fetched/known, so probing it costs
# one bounded extra request regardless of its size, and gating on size there
# caused a confirmed gap of ~3,258 pages hidden behind ~56 index pages a few
# KB over this threshold. See notes/pipeline_upgrade_plan.md's 2026-07-11 gap
# audit.
SUBPAGE_PROBE_MAX_BYTES = 5_000

# Category count from the last successful run's tree.json, used only as a
# rough denominator for the live "N/expected" progress line below the
# scrolling tree output. The real category count for a given run may differ
# (site content changes over time) — this is an estimate, not a hard target.
EXPECTED_CATEGORY_COUNT = 189


class CategoryProgress:
    """
    Prints the category tree live, depth-first, as build_skeleton() discovers
    it (one indented line per category — this *is* the traversal order, so no
    separate tree structure is needed). A single status line stays pinned at
    the bottom via carriage return, showing count vs. EXPECTED_CATEGORY_COUNT,
    plus whatever ActivityStatus.current currently holds (see below) so the
    same line also answers "is it stuck, or is it working right now?".
    """

    def __init__(self, expected_total: int = EXPECTED_CATEGORY_COUNT):
        self.count = 0
        self.expected_total = expected_total
        self._status_len = 0

    def _clear_status(self) -> None:
        if self._status_len:
            sys.stdout.write("\r" + " " * self._status_len + "\r")

    def _write_status(self) -> None:
        pct = (100 * self.count / self.expected_total) if self.expected_total else 0
        line = f"{self.count}/{self.expected_total} categories ({pct:.0f}%) -- {activity.current}"
        # Truncate to the terminal width (minus 1, to avoid the cursor itself
        # forcing a wrap) so a long activity string (e.g. a deeply-nested
        # subpage path) never wraps onto a second row -- a single \r only
        # returns to the start of the *current* visual row, so a wrapped
        # status line can't be cleared correctly and leaves garbage behind.
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
        if width > 1:
            line = line[: width - 1]
        self._status_len = len(line)
        sys.stdout.write(line)
        sys.stdout.flush()

    def visit(self, title: str, depth: int) -> None:
        self.count += 1
        self._clear_status()
        print(f"{'    ' * depth}{to_iast(title)}")
        self._write_status()

    def refresh(self) -> None:
        """Repaint the pinned status line with whatever activity.current says now,
        without advancing the category count or printing a new tree line. Called
        by ActivityStatus itself so every activity change is immediately visible,
        not just on the next category visit."""
        self._clear_status()
        self._write_status()

    def close(self) -> None:
        self._clear_status()
        self._write_status()
        sys.stdout.write("\n")
        sys.stdout.flush()


class ActivityStatus:
    """
    Global single-slot "what is the scraper doing right now" signal, updated by
    api_get() on every request and every rate-limit backoff sleep. Exists because
    a silent scraper is ambiguous between "hung" and "sleeping out a ~30-90s
    Wikimedia Retry-After stall" -- both look identical (no output) without this.

    Bind any sink with a zero-arg `refresh()` that repaints using `.current`
    (CategoryProgress is the only sink now that the crawl is a single pass).
    """

    def __init__(self) -> None:
        self.current = "starting..."
        self._sink = None

    def bind(self, sink) -> None:
        self._sink = sink

    def set(self, text: str) -> None:
        self.current = text
        if self._sink is not None:
            self._sink.refresh()


activity = ActivityStatus()

# Disk-backed cache of raw API responses, keyed by a hash of the request params.
# Survives across runs so re-running the scraper (e.g. during dev iteration, or
# after a rate-limit interruption) doesn't have to re-hit the live API for
# requests already answered. Delete this directory to force a fresh crawl.
API_CACHE_DIR = Path(__file__).parent / ".api_cache"


session = requests.Session()
session.headers.update(
    {
        "User-Agent": "WikisourceCategoryCrawler/3.0 "
        "(https://github.com/tylergneill/sanskrit-wikisource-mirror; polite; single-threaded; research use)"
    }
)

_size_cache: Dict[int, int] = {}          # pageid -> bytes
_timestamp_cache: Dict[int, str] = {}     # pageid -> last-revision ISO 8601 timestamp
_api_cache: Dict[str, dict] = {}          # small memo for identical API calls


# Terminal-output-only: transliterates the scraper's own live progress
# display (category tree lines, status-line activity text) from Devanagari to
# IAST via skrutable, purely for readability while watching a run. Does not
# touch tree.json -- title fields there stay raw Devanagari per CLAUDE.md;
# the frontend does its own client-side transliteration on render.
_transliterator = Transliterator(from_scheme="DEV", to_scheme="IAST")


def to_iast(text: str) -> str:
    return _transliterator.transliterate(text)


def strip_cat_prefix(title: str) -> str:
    s = (title or "").strip()
    if s.startswith("वर्गः:"):
        return s[len("वर्गः:") :].strip()
    if s.startswith("Category:"):
        return s[len("Category:") :].strip()
    return s


def page_url(title: str) -> str:
    # Unicode URL (no percent-escaped ugliness)
    return f"{BASE_URL}/wiki/{title}"


def _cache_path_for_key(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return API_CACHE_DIR / f"{digest}.json"


def _short_params(params: dict) -> str:
    interesting = {k: v for k, v in params.items() if k in ("list", "prop", "cmtitle", "apprefix", "pageids", "titles")}
    return ", ".join(f"{k}={v}" for k, v in interesting.items())


def _activity_label(params: dict) -> str:
    """Compact, human-readable one-liner for the live status line -- unlike
    _short_params (used in --debug logs), this truncates pageids batches to a
    count instead of dumping every id, since a phase-2 batch request can carry
    up to 50 of them and a raw dump would blow out the terminal line."""
    if "cmtitle" in params:
        return f"categorymembers: {to_iast(strip_cat_prefix(params['cmtitle']))}"
    if "apprefix" in params:
        return f"subpages: {to_iast(params['apprefix'].rstrip('/'))}"
    if "pageids" in params:
        n = params["pageids"].count("|") + 1
        return f"page metadata: {n} page(s)"
    return _short_params(params)


def api_get(params: dict, delay_s: float) -> dict:
    # maxlag: standard MediaWiki bot-etiquette signal for non-interactive tasks.
    # See https://www.mediawiki.org/wiki/API:Etiquette
    params = {**params, "maxlag": "5"}

    key = json.dumps(params, sort_keys=True, ensure_ascii=False)
    if key in _api_cache:
        log.debug("MEM-CACHE HIT  %s", _short_params(params))
        activity.set(f"cached: {_activity_label(params)}")
        return _api_cache[key]

    cache_path = _cache_path_for_key(key)
    if cache_path.exists():
        log.debug("DISK-CACHE HIT %s", _short_params(params))
        activity.set(f"cached: {_activity_label(params)}")
        with cache_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _api_cache[key] = data
        return data

    log.debug("NETWORK CALL   %s", _short_params(params))
    activity.set(f"fetching: {_activity_label(params)}")
    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            t0 = time.monotonic()
            time.sleep(delay_s)
            r = session.get(API_URL, params=params, timeout=TIMEOUT_S)
            if r.status_code == 429:
                raise requests.exceptions.HTTPError(
                    f"429 Too Many Requests: {r.url}", response=r
                )
            r.raise_for_status()
            data = r.json()
            _api_cache[key] = data
            API_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            log.debug("  -> OK in %.2fs", time.monotonic() - t0)
            return data
        except Exception as e:
            last_err = e
            resp = getattr(e, "response", None)
            if resp is not None and resp.status_code == 429:
                # Wikimedia's edge rate limiter (Envoy) sends an explicit
                # Retry-After (seconds); honor it exactly rather than guessing.
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait_s = float(retry_after) if retry_after is not None else delay_s * (2 ** attempt)
                except ValueError:
                    wait_s = delay_s * (2 ** attempt)
                log.debug("  -> 429 on attempt %d, sleeping %.1fs (Retry-After=%s)", attempt, wait_s, retry_after)
                _sleep_with_countdown(wait_s, f"RATE-LIMITED: waiting {{remaining}}s (Retry-After={wait_s:.0f}s)")
            else:
                wait_s = delay_s * attempt
                log.debug("  -> error on attempt %d: %r, sleeping %.1fs", attempt, e, wait_s)
                _sleep_with_countdown(wait_s, f"retrying after error: waiting {{remaining}}s")

    raise RuntimeError(f"API request failed after {MAX_RETRIES} retries.\nParams: {params}\n{last_err}")


def _sleep_with_countdown(total_s: float, label_template: str) -> None:
    """Sleep in 1s ticks, updating activity.current each tick so a long
    rate-limit backoff is visibly counting down rather than silently frozen
    (indistinguishable, before this, from the process actually hanging)."""
    remaining = total_s
    while remaining > 0:
        step = min(1.0, remaining)
        activity.set(label_template.format(remaining=f"{remaining:.0f}"))
        time.sleep(step)
        remaining -= step


def category_members(
    cat_title: str,
    delay_s: float,
) -> Tuple[List[dict], List[dict]]:
    """
    Returns (subcats, pages), each element includes: title, pageid, ns.
    """
    subcats: List[dict] = []
    pages: List[dict] = []

    cmcontinue = None
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": cat_title,
            "cmnamespace": "0|14",         # mainspace + category namespace
            "cmtype": "page|subcat",
            "cmlimit": "500",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        data = api_get(params, delay_s=delay_s)
        cms = data.get("query", {}).get("categorymembers", [])
        for m in cms:
            ns = m.get("ns")
            if ns == 14:
                subcats.append(m)
            elif ns == 0:
                pages.append(m)

        cont = data.get("continue", {})
        cmcontinue = cont.get("cmcontinue")
        if not cmcontinue:
            break

    return subcats, pages


def _page_meta_params(missing: List[int]) -> dict:
    return {
        "action": "query",
        "format": "json",
        "prop": "revisions",
        "rvprop": "size|timestamp",
        "pageids": "|".join(str(pid) for pid in missing),
    }


def fetch_page_meta(pageids: List[int], delay_s: float) -> None:
    """
    Populate _size_cache and _timestamp_cache for each pageid,
    using prop=revisions&rvprop=size|timestamp (batch).
    """
    BATCH = 50
    for i in range(0, len(pageids), BATCH):
        batch = pageids[i : i + BATCH]
        missing = [pid for pid in batch if pid not in _size_cache]
        if not missing:
            continue

        params = _page_meta_params(missing)
        data = api_get(params, delay_s=delay_s)
        pages = data.get("query", {}).get("pages", {})

        for pid_str, p in pages.items():
            try:
                pid = int(pid_str)
            except Exception:
                continue
            revs = p.get("revisions") or []
            if revs and "size" in revs[0]:
                _size_cache[pid] = int(revs[0]["size"])
            else:
                _size_cache[pid] = 0
            if revs and "timestamp" in revs[0]:
                _timestamp_cache[pid] = revs[0]["timestamp"]
            else:
                _timestamp_cache[pid] = ""


def fetch_subpages(title: str, delay_s: float) -> List[dict]:
    """
    Returns direct MediaWiki subpages of `title` (namespace 0), i.e. pages
    whose title starts with "<title>/". One level only; does not recurse.

    `delay_s` here is deliberately the caller's subpage-specific delay (see
    --subpage-delay), not the general --delay: this is an unbatched,
    one-request-per-page-probed loop (list=allpages has no cross-title
    batching), so it is the dominant source of request *volume* when
    recursing into deeply-nested works, and was the original cause of
    repeatedly tripping Wikimedia's edge rate limiter -- see CLAUDE.md.
    """
    results: List[dict] = []
    apcontinue = None
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "allpages",
            "apprefix": f"{title}/",
            "apnamespace": "0",
            "aplimit": "500",
        }
        if apcontinue:
            params["apcontinue"] = apcontinue

        data = api_get(params, delay_s=delay_s)
        results.extend(data.get("query", {}).get("allpages", []))

        cont = data.get("continue", {})
        apcontinue = cont.get("apcontinue")
        if not apcontinue:
            break

    return results


def collect_subpages_recursive(
    title: str,
    delay_s: float,
    seen_titles: Set[str],
) -> List[Tuple[str, int]]:
    """
    Recursively discovers all descendant subpages of `title` (any nesting
    depth, e.g. "Title/Part1/Section2"), guarding against repeats via
    seen_titles. Returns a flat list of (page_title, pageid).

    `delay_s` is the subpage-specific delay (see --subpage-delay) and is
    passed straight through to both fetch_subpages and fetch_page_meta calls
    made here, since both are part of the same request-volume-heavy loop.
    """
    found: List[Tuple[str, int]] = []
    direct = fetch_subpages(title, delay_s=delay_s)

    new_pids = [
        int(m["pageid"])
        for m in direct
        if m.get("title") and m.get("pageid") is not None and m["title"] not in seen_titles
    ]
    fetch_page_meta(new_pids, delay_s=delay_s)

    for m in direct:
        sub_title = m.get("title", "")
        pid = m.get("pageid")
        if not sub_title or pid is None or sub_title in seen_titles:
            continue
        seen_titles.add(sub_title)
        pid_int = int(pid)
        found.append((sub_title, pid_int))

        # Only recurse further if this subpage is itself small enough to
        # plausibly be another index/ToC level, same reasoning as build_skeleton.
        if _size_cache.get(pid_int, 0) <= SUBPAGE_PROBE_MAX_BYTES:
            found.extend(collect_subpages_recursive(sub_title, delay_s=delay_s, seen_titles=seen_titles))

    return found


@dataclass
class CatSkeleton:
    title: str              # no "वर्गः:" prefix
    full_title: str         # full MediaWiki title (with namespace)
    id: str                 # path-derived, unique per occurrence in the tree
    subcats: List["CatSkeleton"]
    pages: List[Tuple[str, int]]   # (page_title, pageid)
    points_to: Optional[str] = None  # if set, this occurrence carries no content --
                                      # see the id of the occurrence that does


def cat_id_for_path(path: List[str]) -> str:
    return "cat:" + "/".join(path)


def build_skeleton(
    full_cat_title: str,
    delay_s: float,
    seen_cats: Dict[str, str],   # full_title -> id of the occurrence holding real content
    depth: int,
    path: List[str],
    progress: Optional["CategoryProgress"] = None,
    subpage_delay_s: Optional[float] = None,
) -> CatSkeleton:
    title = strip_cat_prefix(full_cat_title)
    this_path = path + [title]
    this_id = cat_id_for_path(this_path)

    if full_cat_title in seen_cats:
        # Same category, already reached via a different parent (Wikisource's category
        # graph is not a tree). This occurrence carries no content of its own -- it
        # points at the occurrence that does. Both occurrences are equally "real"
        # filings of the category; which one holds the content is purely an artifact
        # of crawl order, not a canonical/alias distinction.
        return CatSkeleton(
            title=title,
            full_title=full_cat_title,
            id=this_id,
            subcats=[],
            pages=[],
            points_to=seen_cats[full_cat_title],
        )
    seen_cats[full_cat_title] = this_id

    if progress is not None:
        progress.visit(strip_cat_prefix(full_cat_title), depth)

    subcat_members, page_members = category_members(full_cat_title, delay_s=delay_s)

    sub_skeletons: List[CatSkeleton] = []
    for m in subcat_members:
        sub_full = m.get("title", "")
        if not sub_full:
            continue
        
        # Exclude internal/maintenance categories
        stripped = strip_cat_prefix(sub_full)
        if stripped in ["अवैध एचटीएमएल टैग का उपयोग कर रहे पृष्ठ", "अनिर्दिष्टानि पुटानि", "निष्कासनाय"]:
            continue

        sub_skeletons.append(
            build_skeleton(
                sub_full,
                delay_s=delay_s,
                seen_cats=seen_cats,
                depth=depth + 1,
                path=this_path,
                progress=progress,
                subpage_delay_s=subpage_delay_s,
            )
        )

    pages: List[Tuple[str, int]] = []
    seen_titles: Set[str] = {t for t, _ in pages}
    effective_subpage_delay = subpage_delay_s if subpage_delay_s is not None else delay_s

    # Fetch sizes up front for this batch of pages, so we can skip the (costly)
    # subpage probe for pages that are clearly real content, not index/ToC pages.
    # This is also what makes phase 2 (the old standalone page-meta pass) redundant:
    # by the time the whole tree walk finishes, every page's metadata was already
    # fetched here, inline, as it was discovered.
    direct_pageids = [int(m["pageid"]) for m in page_members if m.get("pageid") is not None]
    fetch_page_meta(direct_pageids, delay_s=effective_subpage_delay)

    for m in page_members:
        t = m.get("title", "")
        pid = m.get("pageid")
        if not t or pid is None or t in seen_titles:
            continue
        pid_int = int(pid)
        seen_titles.add(t)
        pages.append((t, pid_int))

        # Recurse into MediaWiki subpages (e.g. an index page's "Title/सर्गः" children),
        # since categorymembers only sees the index page itself, not its subpages.
        # This loop is unbatched (one list=allpages request per page probed) and was
        # the original source of repeatedly tripping Wikimedia's edge rate limiter on
        # deeply-nested works (e.g. बैबल्) -- see CLAUDE.md. Mitigated via a dedicated
        # --subpage-delay pace, not by skipping the work; occasional 429s are expected
        # and handled by api_get's Retry-After backoff.
        #
        # No size gate here, deliberately: unlike the recursive descent below (which
        # is unbounded and would roughly double request volume if applied to every
        # real content page it finds), every page here is already a known
        # categorymember -- the probe is one extra already-"paid for" request per
        # page, not exponential. Gating on size here caused a confirmed gap: ~56
        # legitimate index/ToC pages (e.g. अग्निपुराणम् at 20,836B) sit a few KB over
        # SUBPAGE_PROBE_MAX_BYTES and were silently never probed, hiding ~3,258 real
        # subpages underneath them. See notes/pipeline_upgrade_plan.md's 2026-07-11
        # gap audit.
        subpages = collect_subpages_recursive(t, delay_s=effective_subpage_delay, seen_titles=seen_titles)
        for sub_t, sub_pid in subpages:
            pages.append((sub_t, sub_pid))

    sub_skeletons.sort(key=lambda x: x.title)
    pages.sort(key=lambda x: x[0])

    return CatSkeleton(
        title=title,
        full_title=full_cat_title,
        id=this_id,
        subcats=sub_skeletons,
        pages=pages,
    )


def page_id(title: str) -> str:
    return f"page:{title}"


# Estimated wikitext markup/boilerplate overhead as a function of raw page
# size, fit by linear regression (overhead_bytes = a + b * raw_bytes) against
# a live sample of 70 real Wikisource content pages (84 sampled, 14 excluded
# as non-content: redirects, deletion-request stubs, bare <pages/> transclusion
# pointers -- ~17% of the sample, itself a separate finding worth noting: a
# meaningful share of nominal "pages" carry no real text at all). R^2 = 0.956.
# This is a whole-corpus estimate, NOT a per-page measurement -- getting exact
# per-page content bytes would require fetching and parsing every page's real
# wikitext (rvprop=content), which this scraper deliberately does not do (see
# CLAUDE.md: metadata-only, no page bodies fetched). Individual pages, especially
# outliers like the excluded non-content pages above, will be off; this is meant
# to turn "raw MediaWiki wikitext bytes" (dominated by markup, not comparable
# across pages of different structure) into a roughly-meaningful "how much actual
# Devanagari text is here" number, not to be exact.
CONTENT_OVERHEAD_INTERCEPT = 413.49
CONTENT_OVERHEAD_SLOPE = 0.04558


def estimate_content_bytes(raw_bytes: int) -> int:
    """Estimate real Devanagari-content bytes from raw MediaWiki wikitext
    bytes, subtracting the regression-estimated markup/boilerplate overhead.
    See CONTENT_OVERHEAD_INTERCEPT/SLOPE above for what this is and isn't."""
    overhead = CONTENT_OVERHEAD_INTERCEPT + CONTENT_OVERHEAD_SLOPE * raw_bytes
    return max(0, round(raw_bytes - overhead))


# Devanagari is consistently ~1.975x the UTF-8 byte size of IAST for running
# Sanskrit verse text (Devanagari is 3 bytes/codepoint in UTF-8; IAST is
# mostly 1-2 bytes/codepoint). Measured empirically by transliterating the
# full Mahabharata critical-edition corpus (~224,740 lines, skrutable's
# scheme_detection_mbh_corpus.txt) from IAST to Devanagari and comparing
# UTF-8 byte counts: mean/median ratio 1.975/1.973, stdev 0.0245 across
# 500-line chunks (p10-p90: 1.945-2.009) -- tight and stable, since it's
# driven by encoding mechanics, not content variation. Source data here is
# always Devanagari (see "What this is" above); this ratio converts the
# content-byte estimate into an IAST-equivalent figure without actually
# transliterating anything server-side.
DEVANAGARI_TO_IAST_BYTE_RATIO = 1.975


def estimate_iast_bytes(content_bytes_est: int) -> int:
    """Convert an estimated Devanagari-content byte count into an
    IAST-equivalent byte count via DEVANAGARI_TO_IAST_BYTE_RATIO. This is
    the number the frontend actually displays -- see CLAUDE.md."""
    return round(content_bytes_est / DEVANAGARI_TO_IAST_BYTE_RATIO)


def skeleton_to_json(node: CatSkeleton) -> dict:
    """Build the raw JSON tree (structure + pointers), without stats -- stats are filled
    in afterward by attach_stats(), which needs the whole id->node map built first so it
    can dedupe shared categories reachable through more than one child."""
    if node.points_to is not None:
        # Same category, filed under more than one parent (Wikisource's category graph
        # is not a tree). This occurrence carries no children/pages of its own -- both
        # occurrences are equally real filings of the category; `points_to` just says
        # where the (arbitrarily, by crawl order) inlined children/pages live, so
        # nothing is duplicated in the JSON. Despite carrying no children/pages here,
        # this occurrence still gets its own `stats` (see attach_stats) -- from the
        # sidebar's point of view every occurrence is equally real and shows real numbers.
        return {
            "id": node.id,
            "type": "category-pointer",
            "title": node.title,
            "points_to": node.points_to,
        }

    children_json = [skeleton_to_json(ch) for ch in node.subcats]

    pages_json = []
    for t, pid in node.pages:
        b = _size_cache.get(pid, 0)
        ts = _timestamp_cache.get(pid, "")
        content_bytes = estimate_content_bytes(b)
        pages_json.append(
            {
                "id": page_id(t),
                "type": "page",
                "title": t,
                "url": page_url(t),
                "stats": {
                    "bytes": b,
                    "content_bytes_est": content_bytes,
                    "iast_bytes_est": estimate_iast_bytes(content_bytes),
                    "last_changed": ts,
                },
            }
        )

    return {
        "id": node.id,
        "type": "category",
        "title": node.title,
        "children": children_json,
        "pages": pages_json,
    }


def attach_stats(root: dict) -> None:
    """Fill in `stats` on every category/category-pointer node via a deduped page-id-set
    walk, so a shared category's bytes/pages are counted exactly once at whatever
    ancestor the two occurrences' paths actually converge -- not double-counted (if the
    convergence point summed naive per-child totals) and not under-counted (if a shared
    category were skipped entirely). Recomputing the full descendant page set per node
    (rather than reusing children's precomputed sets) is what makes this correct
    regardless of how far apart two occurrences of a shared category sit in the tree;
    nodes below the lowest common ancestor of any sharing are unaffected either way.

    Every category-pointer node also gets its own `stats` (same numbers as the
    occurrence it points to) -- both occurrences are equally real, so both show real
    numbers in the UI, not just the one that happens to carry the inlined content.
    """
    by_id: Dict[str, dict] = {}

    def index(n: dict) -> None:
        by_id[n["id"]] = n
        for ch in n.get("children", []):
            index(ch)

    index(root)

    # Memoized: node id -> {page_id: (bytes, content_bytes_est, iast_bytes_est,
    # last_changed)} for every distinct page reachable from that node (pointer
    # nodes resolve to their target's page set).
    memo: Dict[str, Dict[str, Tuple[int, int, int, str]]] = {}

    def collect(node_id: str) -> Dict[str, Tuple[int, int, int, str]]:
        if node_id in memo:
            return memo[node_id]
        node = by_id[node_id]
        if node["type"] == "category-pointer":
            result = collect(node["points_to"])
            memo[node_id] = result
            return result

        merged: Dict[str, Tuple[int, int, int, str]] = {
            p["id"]: (
                int(p["stats"]["bytes"] or 0),
                int(p["stats"]["content_bytes_est"] or 0),
                int(p["stats"]["iast_bytes_est"] or 0),
                str(p["stats"]["last_changed"] or ""),
            )
            for p in node.get("pages", [])
        }
        for ch in node.get("children", []):
            merged.update(collect(ch["id"]))
        memo[node_id] = merged
        return merged

    for node_id in by_id:
        pages = collect(node_id)
        by_id[node_id]["stats"] = {
            "bytes": sum(b for b, _, _, _ in pages.values()),
            "content_bytes_est": sum(c for _, c, _, _ in pages.values()),
            "iast_bytes_est": sum(i for _, _, i, _ in pages.values()),
            "count": len(pages),
            "last_changed": max((ts for _, _, _, ts in pages.values()), default=""),
        }


_DIGIT_RUN_RE = re.compile(r"\d+", re.UNICODE)


def _natural_sort_key(title: str) -> tuple:
    """Split on runs of digits (any Unicode script, e.g. Devanagari ०-९) so titles
    sort numerically within each run instead of lexicographically -- "अध्यायः ३३"
    before "अध्यायः ३२६", not after. Each digit run becomes (its integer value,
    ""); non-digit spans become (-1, the span itself) so digit vs. non-digit
    chunks never get compared against each other's wrong-typed value."""
    parts = _DIGIT_RUN_RE.split(title)
    digit_runs = _DIGIT_RUN_RE.findall(title)
    key: List[Tuple[int, str]] = []
    for i, part in enumerate(parts):
        if part:
            key.append((-1, part))
        if i < len(digit_runs):
            value = int("".join(str(unicodedata.digit(ch)) for ch in digit_runs[i]))
            key.append((value, ""))
    return tuple(key)


def sort_tree(node: dict) -> None:
    """Sort children/pages in place by natural-sorted title, recursively. Runs once
    on the final assembled JSON dict, so it's cache-transparent -- reruns from a warm
    .api_cache/ re-sort identically regardless of crawl order."""
    if "children" in node:
        node["children"].sort(key=lambda n: _natural_sort_key(n["title"]))
        for ch in node["children"]:
            sort_tree(ch)
    if "pages" in node:
        node["pages"].sort(key=lambda n: _natural_sort_key(n["title"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY_S)
    ap.add_argument(
        "--subpage-delay",
        type=float,
        default=None,
        help="Delay (seconds) between MediaWiki subpage-discovery (list=allpages) requests "
        "and their associated page-meta fetches, separate from --delay. This loop is "
        "unbatched (one request per page probed), making it the dominant source of request "
        "volume -- and thus the main trigger of Wikimedia's edge rate limiter -- when "
        "descending into deeply-nested works (e.g. बैबल्); see CLAUDE.md. Defaults to --delay "
        "if unset.",
    )
    ap.add_argument("--debug", action="store_true", help="log every API call (cache hit/miss, timing, retries) to stderr")
    args = ap.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Build full category skeleton starting at ग्रन्थाः (but we will not output the top ग्रन्थाः node).
    # Page metadata (size/timestamp) is fetched inline as each page is discovered
    # (see build_skeleton's up-front fetch_page_meta call), so there is no separate
    # page-metadata pass afterward -- this single walk is the whole crawl.
    print("Walking category tree (live, depth-first) ...")
    seen_cats: Dict[str, str] = {}

    progress = CategoryProgress()
    activity.bind(progress)

    granth_skel = build_skeleton(
        GRANTHAH_CAT,
        delay_s=args.delay,
        seen_cats=seen_cats,
        depth=0,
        path=[],
        progress=progress,
        subpage_delay_s=args.subpage_delay,
    )

    # Inject धर्मशास्त्रम् as an extra top-level child under ग्रन्थाः (as requested),
    # even if it isn't actually listed as a subcategory there.
    has_dharma = any(ch.title == strip_cat_prefix(DHARMASHASTRA_CAT) for ch in granth_skel.subcats)
    if not has_dharma:
        dh_seen: Dict[str, str] = dict(seen_cats)
        dh_skel = build_skeleton(
            DHARMASHASTRA_CAT,
            delay_s=args.delay,
            seen_cats=dh_seen,
            depth=1,
            path=[],
            progress=progress,
            subpage_delay_s=args.subpage_delay,
        )
        # Merge seen sets (safe; categories might overlap)
        seen_cats = dh_seen
        granth_skel.subcats.append(dh_skel)

    progress.close()
    activity.bind(None)

    # Output: omit the top "ग्रन्थाः" level, and omit "वर्गः:" prefixes everywhere (already stripped)
    # We use skeleton_to_json on the root node to get its full structure, then extract what we need.
    root_data = skeleton_to_json(granth_skel)

    out = {
        "root": {
            "id": "root",
            "title": "ग्रन्थाः (धर्मशास्त्राणि च)",
            "type": "collection",
            "children": root_data["children"],
            "pages": root_data["pages"],
        }
    }
    attach_stats(out["root"])
    sort_tree(out["root"])

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=4)

    _stamp_data_version()


def _stamp_data_version() -> None:
    """Record today's date as __data_version__ in docs/VERSION (second line),
    alongside the existing __version__ (code version, bumped manually/separately)."""
    version_path = Path(__file__).resolve().parent / "docs" / "VERSION"
    today = time.strftime("%Y-%m-%d", time.gmtime())
    lines = version_path.read_text(encoding="utf-8").splitlines() if version_path.exists() else []
    lines = [ln for ln in lines if not ln.startswith("__data_version__")]
    lines.append(f'__data_version__ = "{today}"')
    version_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
