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

# ProofreadPage extension namespaces: Index: (scanned-book metadata/ToC, one
# per scanned work) and Page: (one per scanned page's OCR/proofread text).
# sa.wikisource.org's localized namespace names, not the English aliases --
# apnamespace is numeric so this only matters for building prefixes below.
INDEX_NS = 106
PAGE_NS = 104
INDEX_PREFIX = "अनुक्रमणिका:"
PAGE_PREFIX = "पृष्ठम्:"

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

# Fallback category count, used only as a rough denominator for the live
# "N/expected" progress line below the scrolling tree output when no prior
# tree.json exists to read a real count from (see expected_category_count()
# below, which is what's actually used in the common case). The real category
# count for a given run may differ (site content changes over time) -- this
# is always just an estimate, never a hard target.
EXPECTED_CATEGORY_COUNT_FALLBACK = 189


def expected_category_count(out_path: str) -> int:
    """
    Category count from the previous run's output file at `out_path`, so the
    live progress line's denominator stays roughly accurate run over run
    without manual updates. Falls back to EXPECTED_CATEGORY_COUNT_FALLBACK if
    there's no prior file yet (e.g. first-ever run) or it can't be read.

    Counts only "category" nodes, not "category-pointer" ones -- matches
    CategoryProgress.visit(), which build_skeleton() only calls on a
    category's first-seen occurrence (see the seen_cats early-return there);
    repeat filings of an already-seen category never advance the live
    counter, so including category-pointer nodes here would inflate the
    denominator past what the numerator can ever reach.
    """
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return EXPECTED_CATEGORY_COUNT_FALLBACK

    count = 0

    def walk(node: dict) -> None:
        nonlocal count
        if node.get("type") == "category":
            count += 1
        for c in node.get("children", []):
            walk(c)

    walk(data.get("root", {}))
    return count or EXPECTED_CATEGORY_COUNT_FALLBACK


class CategoryProgress:
    """
    Prints the category tree live, depth-first, as build_skeleton() discovers
    it (one indented line per category — this *is* the traversal order, so no
    separate tree structure is needed). A single status line stays pinned at
    the bottom via carriage return, showing count vs. EXPECTED_CATEGORY_COUNT,
    plus whatever ActivityStatus.current currently holds (see below) so the
    same line also answers "is it stuck, or is it working right now?".
    """

    def __init__(self, expected_total: int = EXPECTED_CATEGORY_COUNT_FALLBACK):
        self.count = 0
        self.expected_total = expected_total
        self._status_len = 0

    def _clear_status(self) -> None:
        if self._status_len:
            sys.stdout.write("\r" + " " * self._status_len + "\r")

    def _write_status(self) -> None:
        pct = (100 * self.count / self.expected_total) if self.expected_total else 0
        line = f"{self.count}/{self.expected_total} categories ({pct:.0f}%) -- {activity.display}"
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


class SimpleStatusLine:
    """
    Minimal pinned single-line ActivityStatus sink, for phases of the run that
    aren't the category tree walk (e.g. the unpublished-OCR sweep) and so
    don't have a meaningful "N/expected categories" count to show -- just
    repaints activity.current on a carriage-returned line.
    """

    def __init__(self) -> None:
        self._status_len = 0

    def refresh(self) -> None:
        if self._status_len:
            sys.stdout.write("\r" + " " * self._status_len + "\r")
        line = activity.display
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
        if width > 1:
            line = line[: width - 1]
        self._status_len = len(line)
        sys.stdout.write(line)
        sys.stdout.flush()

    def close(self) -> None:
        self.refresh()
        sys.stdout.write("\n")
        sys.stdout.flush()


class ActivityStatus:
    """
    Global single-slot "what is the scraper doing right now" signal, updated by
    api_get() on every request and every rate-limit backoff sleep. Exists because
    a silent scraper is ambiguous between "hung" and "sleeping out a ~30-90s
    Wikimedia Retry-After stall" -- both look identical (no output) without this.

    Bind any sink with a zero-arg `refresh()` that repaints using `.current`
    (CategoryProgress for the category-tree walk, SimpleStatusLine for other
    phases like the unpublished-OCR sweep).

    `prefix` is a SEPARATE, longer-lived slot from `.current` -- a caller
    doing a multi-step sweep over a known-size batch (e.g.
    build_orphan_bucket's "checking subpages N/M" loop) sets it once per
    outer-loop item via set_prefix(), and it survives every inner api_get()
    call's own set(), which overwrites `.current` on literally every single
    request (see api_get below) and would otherwise clobber an "N/M" counter
    before it ever got a chance to paint -- confirmed live: without this, the
    orphan-sweep counters added alongside this class were invisible in
    practice, always immediately overwritten by the next "fetching:"/
    "cached:" label from the very api_get call they were meant to describe
    progress *around*. `.current` (set via set()) is still shown in full,
    just with `prefix` prepended, so per-request detail isn't lost either.
    """

    def __init__(self) -> None:
        self.current = "starting..."
        self.prefix = ""
        self._sink = None

    def bind(self, sink) -> None:
        self._sink = sink

    def set(self, text: str) -> None:
        self.current = text
        if self._sink is not None:
            self._sink.refresh()

    def set_prefix(self, text: str) -> None:
        self.prefix = text
        if self._sink is not None:
            self._sink.refresh()

    @property
    def display(self) -> str:
        return f"{self.prefix} -- {self.current}" if self.prefix else self.current


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


@dataclass
class PageSkeleton:
    title: str
    pageid: int
    subpages: List["PageSkeleton"]


def collect_subpages_recursive(
    title: str,
    delay_s: float,
    seen_titles: Set[str],
) -> List[PageSkeleton]:
    """
    Recursively discovers all descendant subpages of `title` (any nesting
    depth, e.g. "Title/Part1/Section2"), guarding against repeats via
    seen_titles. Returns the *direct* subpages of `title` as a list of
    PageSkeleton, each carrying its own further descendants in its
    `subpages` field -- i.e. this returns a tree, not a flattened list, so
    the graph's real parent/child structure (which page is a subpage of
    which) survives into tree.json instead of being collapsed into flat
    siblings. See notes/pipeline_upgrade_plan.md, item 3's low-hanging-fruit
    scope: nest only what the graph already states, no curatorial judgment.

    `delay_s` is the subpage-specific delay (see --subpage-delay) and is
    passed straight through to both fetch_subpages and fetch_page_meta calls
    made here, since both are part of the same request-volume-heavy loop.
    """
    direct = fetch_subpages(title, delay_s=delay_s)

    new_pids = [
        int(m["pageid"])
        for m in direct
        if m.get("title") and m.get("pageid") is not None and m["title"] not in seen_titles
    ]
    fetch_page_meta(new_pids, delay_s=delay_s)

    result: List[PageSkeleton] = []
    for m in direct:
        sub_title = m.get("title", "")
        pid = m.get("pageid")
        if not sub_title or pid is None or sub_title in seen_titles:
            continue
        seen_titles.add(sub_title)
        pid_int = int(pid)

        # Only recurse further if this subpage is itself small enough to
        # plausibly be another index/ToC level, same reasoning as build_skeleton.
        grandchildren: List[PageSkeleton] = []
        if _size_cache.get(pid_int, 0) <= SUBPAGE_PROBE_MAX_BYTES:
            grandchildren = collect_subpages_recursive(sub_title, delay_s=delay_s, seen_titles=seen_titles)

        result.append(PageSkeleton(title=sub_title, pageid=pid_int, subpages=grandchildren))

    return result


def fetch_index_pages(delay_s: float) -> List[dict]:
    """
    Returns every Index: (ns 106, अनुक्रमणिका) page -- one per scanned book on
    sa.wikisource.org, regardless of whether it's ever been transcluded into
    mainspace. Each element includes title, pageid, ns.
    """
    results: List[dict] = []
    apcontinue = None
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "allpages",
            "apnamespace": str(INDEX_NS),
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


def is_transcluded_in_mainspace(index_title: str, delay_s: float) -> bool:
    """
    True if this Index: page's scan has been transcluded into at least one
    mainspace (ns 0) page -- i.e. the finished/proofread version already
    exists and is reachable via the normal category walk, so the raw scan
    would be a duplicate if also surfaced here.
    """
    params = {
        "action": "query",
        "format": "json",
        "list": "embeddedin",
        "eititle": index_title,
        "einamespace": "0",
        "eilimit": "1",
    }
    data = api_get(params, delay_s=delay_s)
    return bool(data.get("query", {}).get("embeddedin"))


def fetch_ocr_pages(index_title: str, delay_s: float) -> List[dict]:
    """
    Returns every Page: (ns 104, पृष्ठम्) subpage of a given Index: work, i.e.
    its individual scanned/OCR'd pages. Same allpages-prefix pattern as
    fetch_subpages, just against ns 104 instead of ns 0.
    """
    # Index: titles carry their own "अनुक्रमणिका:" prefix already; Page: titles
    # for the same work are "पृष्ठम्:<title without index prefix>/<n>".
    bare_title = index_title[len(INDEX_PREFIX):] if index_title.startswith(INDEX_PREFIX) else index_title
    results: List[dict] = []
    apcontinue = None
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "allpages",
            "apnamespace": str(PAGE_NS),
            "apprefix": f"{bare_title}/",
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


def build_unpublished_ocr_bucket(delay_s: float, progress: Optional["CategoryProgress"] = None) -> Optional[dict]:
    """
    Sweeps ns 106 (Index:) for every scanned work, skips any already
    transcluded into mainspace (already visible elsewhere in tree.json via
    its finished page), and for the rest fetches its ns 104 (Page:) scans as
    a distinct "second tier" bucket -- Tyler's 2026-07-11 design (see
    notes/pipeline_upgrade_plan.md): don't duplicate published works, but
    don't silently drop fully-unpublished scans either.

    Returns a raw dict (not a CatSkeleton) shaped like a category node but
    with type "ocr-collection"/"ocr-work"/"ocr-page" so the frontend can
    render it like a category tree while still visually distinguishing it as
    unpublished OCR, not curated mainspace content. Returns None if every
    Index: work turns out to already be transcluded (nothing to surface).
    """
    index_pages = fetch_index_pages(delay_s=delay_s)

    works: List[dict] = []
    for ip in index_pages:
        title = ip.get("title", "")
        if not title:
            continue
        if progress is not None:
            activity.set(f"checking OCR transclusion: {to_iast(title)}")
        if is_transcluded_in_mainspace(title, delay_s=delay_s):
            continue

        ocr_pages = fetch_ocr_pages(title, delay_s=delay_s)
        pageids = [int(p["pageid"]) for p in ocr_pages if p.get("pageid") is not None]
        fetch_page_meta(pageids, delay_s=delay_s)

        # "subpages", not "pages" -- matches the mainspace convention (see
        # page_skeleton_to_json) where a container's nested leaves live under
        # `subpages`, so the frontend's collapsible sub-list rendering handles
        # both OCR and non-OCR nesting with the same code path. Individual
        # scans (ocr-page) are themselves leaves with no further nesting, but
        # still carry an empty `subpages: []` for shape parity.
        subpages_json = []
        for p in ocr_pages:
            t = p.get("title", "")
            pid = p.get("pageid")
            if not t or pid is None:
                continue
            pid_int = int(pid)
            b = _size_cache.get(pid_int, 0)
            ts = _timestamp_cache.get(pid_int, "")
            subpages_json.append(
                {
                    "id": page_id(t),
                    "type": "ocr-page",
                    "title": t,
                    "url": page_url(t),
                    "stats": {"bytes": b, "last_changed": ts},
                    "subpages": [],
                }
            )
        if not subpages_json:
            continue

        bare_title = title[len(INDEX_PREFIX):] if title.startswith(INDEX_PREFIX) else title
        works.append(
            {
                "id": f"ocr-work:{bare_title}",
                "type": "ocr-work",
                "title": bare_title,
                "index_url": page_url(title),
                "subpages": subpages_json,
            }
        )

    if not works:
        return None

    works.sort(key=lambda w: w["title"])
    return {
        "id": "ocr-collection",
        "type": "ocr-collection",
        "title": "अप्रकाशित-OCR-कृतयः",
        "children": works,
    }


def fetch_all_mainspace_titles(delay_s: float, progress: Optional["SimpleStatusLine"] = None) -> List[dict]:
    """
    Returns every ns 0 (mainspace) page on sa.wikisource.org via a full
    list=allpages sweep, regardless of category membership -- the only way to
    find pages the top-down category walk (build_skeleton, rooted at
    ग्रन्थाः/धर्मशास्त्रम्) can never reach at all: zero-category pages, and
    pages whose only category tag is itself orphaned (filed under no parent).
    See notes/pipeline_upgrade_plan.md's 2026-07-11 gap audit, causes (b)/(c).
    Each element includes title, pageid, ns.
    """
    results: List[dict] = []
    apcontinue = None
    while True:
        if progress is not None:
            activity.set(f"sweeping mainspace: {len(results)} titles so far")
        params = {
            "action": "query",
            "format": "json",
            "list": "allpages",
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


def collect_placed_titles(node: dict, out: Set[str]) -> None:
    """Walks an already-built tree.json-shaped dict (root category node or
    OCR bucket) and collects every mainspace page title already placed
    somewhere in it -- top-level `pages` and their nested `subpages`, at any
    depth. This is the "already reached" side of the diff against
    fetch_all_mainspace_titles(); anything left over after subtracting this
    set is a genuine gap, not a duplicate of content already in the tree."""
    for ch in node.get("children", []):
        collect_placed_titles(ch, out)
    for p in node.get("pages", []):
        collect_placed_titles(p, out)
    for sp in node.get("subpages", []):
        collect_placed_titles(sp, out)
    idx = node.get("index_page")
    if idx is not None:
        collect_placed_titles(idx, out)
    if node.get("type") in ("page",):
        out.add(node["title"])


# Titles that look like real corpus content but aren't -- meta/infrastructure
# pages (Author:, Main Page, etc.) and non-Sanskrit content that legitimately
# has no place in this mirror. Confirmed via 2026-07-11 gap audit: only ~59
# of ~13,635 leftover mainspace titles matched this pattern (Author: prefix,
# or first character not in Devanagari's Unicode block) -- everything else in
# the leftover set is ordinary in-scope Devanagari content. Deliberately
# narrow: a blanket "looks non-Sanskrit" heuristic would wrongly exclude real
# content (see notes/pipeline_upgrade_plan.md -- the original alphabetical
# first-sample pass wrongly called ~6,653 titles out-of-scope this way).
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def is_out_of_scope_title(title: str) -> bool:
    if title.startswith("Author:"):
        return True
    return not _DEVANAGARI_RE.search(title)


def fetch_page_categories(
    pageids: List[int],
    delay_s: float,
    progress: Optional["SimpleStatusLine"] = None,
) -> Dict[int, List[str]]:
    """
    Returns pageid -> list of full category titles ("वर्गः:...") the page
    carries, via prop=categories (batched). Used to find which orphaned
    category (if any) a leftover mainspace page belongs to.
    """
    result: Dict[int, List[str]] = {}
    BATCH = 50
    total_batches = (len(pageids) + BATCH - 1) // BATCH
    for i in range(0, len(pageids), BATCH):
        batch = pageids[i : i + BATCH]
        if progress is not None:
            activity.set_prefix(f"orphan candidate categories: batch {i // BATCH + 1}/{total_batches}")
        params = {
            "action": "query",
            "format": "json",
            "prop": "categories",
            "cllimit": "500",
            "pageids": "|".join(str(pid) for pid in batch),
        }
        data = api_get(params, delay_s=delay_s)
        pages = data.get("query", {}).get("pages", {})
        for pid_str, p in pages.items():
            try:
                pid = int(pid_str)
            except Exception:
                continue
            cats = [c["title"] for c in p.get("categories", []) if c.get("title")]
            result[pid] = cats
    return result


def build_orphan_bucket(
    delay_s: float,
    subpage_delay_s: float,
    seen_cats: Dict[str, str],
    placed_titles: Set[str],
    progress: Optional["SimpleStatusLine"] = None,
) -> Optional[dict]:
    """
    Finds mainspace content unreachable from ग्रन्थाः/धर्मशास्त्रम् by any
    depth of category/subpage descent -- either because the page carries no
    category tag at all, or because its category tag points to a category
    that is itself never filed under any parent (an orphaned category, e.g.
    वर्गः:महाभारतम्/सभापर्व -- confirmed live to hold the whole Mahābhārata
    parvan-category structure, invisible to a root-down walk no matter how
    deep). See notes/pipeline_upgrade_plan.md's 2026-07-11 gap audit, causes
    (b) and (c).

    `seen_cats` is the SAME dict build_skeleton() populated during the main
    crawl -- reused (and further mutated) here so:
      (1) "is this category orphaned" is just "not already a key in seen_cats"
          after the main walk, no separate reachability check needed, and
      (2) orphan category roots discovered here run through the exact same
          build_skeleton() recursion, so a category shared between two orphan
          clusters (or, in principle, one that turns out to also be reachable
          from a still-unwalked part of the main tree) gets the same
          category-pointer/points_to multi-parent handling for free.

    Returns a category-shaped node (type "category", reusing the ordinary
    schema per the decision not to invent a new node type for this) titled
    "अवर्गीकृतम्" holding one child per discovered orphan-category root plus a
    flat "pages" list for genuinely zero-category leftovers. Returns None if
    every mainspace title turns out to already be placed.
    """
    if progress is not None:
        activity.set_prefix("")
        activity.set("sweeping full mainspace (list=allpages) ...")
    all_titles = fetch_all_mainspace_titles(delay_s=delay_s, progress=progress)

    leftover = [
        m for m in all_titles
        if m.get("title") and m.get("pageid") is not None and m["title"] not in placed_titles
    ]
    leftover = [m for m in leftover if not is_out_of_scope_title(m["title"])]
    if not leftover:
        return None

    leftover_pids = [int(m["pageid"]) for m in leftover]
    if progress is not None:
        activity.set(f"fetching categories for {len(leftover_pids)} orphan candidate(s) ...")
    cats_by_pid = fetch_page_categories(leftover_pids, delay_s=delay_s, progress=progress)

    # Titles with a "|"-free bare form let orphaned-category subtrees (walked
    # via build_skeleton below) discover the SAME pages again through
    # categorymembers -- that's fine and expected, it's what lets a
    # partially-orphaned cluster (some members reachable a different way,
    # some not) come out complete rather than only the leftover fragment.
    orphan_roots: Dict[str, None] = {}   # ordered set of full category titles to crawl
    zero_cat_leftover: List[dict] = []
    for m in leftover:
        pid = int(m["pageid"])
        cats = cats_by_pid.get(pid, [])
        cats = [c for c in cats if strip_cat_prefix(c) not in
                ["अवैध एचटीएमएल टैग का उपयोग कर रहे पृष्ठ", "अनिर्दिष्टानि पुटानि", "निष्कासनाय"]]
        orphan_cats = [c for c in cats if c not in seen_cats]
        if orphan_cats:
            for c in orphan_cats:
                orphan_roots.setdefault(c, None)
        else:
            zero_cat_leftover.append(m)

    orphan_skeletons: List[CatSkeleton] = []
    total_orphan_roots = len(orphan_roots)
    for root_i, full_cat_title in enumerate(orphan_roots, start=1):
        # A category discovered from an earlier root in this same loop may
        # already have been swept into seen_cats by then -- build_skeleton
        # handles that itself (category-pointer), but skip re-walking it as
        # a *root* here since it's no longer orphaned-from-root at this point
        # in the loop.
        if full_cat_title in seen_cats:
            continue
        if progress is not None:
            activity.set_prefix(f"orphaned category {root_i}/{total_orphan_roots}")
        orphan_skeletons.append(
            build_skeleton(
                full_cat_title,
                delay_s=delay_s,
                seen_cats=seen_cats,
                depth=0,
                path=[],
                progress=None,
                subpage_delay_s=subpage_delay_s,
            )
        )

    # Zero-category leftover pages: same subpage-descent treatment as any
    # other top-level categorymember page (build_skeleton's page_members
    # loop), just not reached via categorymembers at all -- reached directly
    # from the allpages sweep instead.
    zero_cat_pids = [int(m["pageid"]) for m in zero_cat_leftover]
    fetch_page_meta(zero_cat_pids, delay_s=subpage_delay_s)
    zero_cat_seen_titles: Set[str] = set()
    zero_cat_pages: List[PageSkeleton] = []
    total_zero_cat = len(zero_cat_leftover)
    for zc_i, m in enumerate(zero_cat_leftover, start=1):
        t = m["title"]
        if t in zero_cat_seen_titles:
            continue
        zero_cat_seen_titles.add(t)
        pid = int(m["pageid"])
        if progress is not None:
            activity.set_prefix(f"zero-category page subpages {zc_i}/{total_zero_cat}")
        subpages = collect_subpages_recursive(t, delay_s=subpage_delay_s, seen_titles=zero_cat_seen_titles)
        zero_cat_pages.append(PageSkeleton(title=t, pageid=pid, subpages=subpages))

    if progress is not None:
        activity.set_prefix("")

    if not orphan_skeletons and not zero_cat_pages:
        return None

    orphan_skeletons.sort(key=lambda x: x.title)
    zero_cat_pages.sort(key=lambda x: x.title)

    bucket_id = "cat:अवर्गीकृतम्"
    children_json = [skeleton_to_json(ch) for ch in orphan_skeletons]
    pages_json = [page_skeleton_to_json(p, bucket_id) for p in zero_cat_pages]

    return {
        "id": bucket_id,
        "type": "category",
        "title": "अवर्गीकृतम्",
        "children": children_json,
        "pages": pages_json,
    }


@dataclass
class CatSkeleton:
    title: str              # no "वर्गः:" prefix
    full_title: str         # full MediaWiki title (with namespace)
    id: str                 # path-derived, unique per occurrence in the tree
    subcats: List["CatSkeleton"]
    pages: List[PageSkeleton]      # top-level pages only; each carries its own nested subpages
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

    pages: List[PageSkeleton] = []
    seen_titles: Set[str] = set()
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
        #
        # Subpages nest under this page (PageSkeleton.subpages), rather than being
        # flattened as extra category-level siblings -- the graph already states this
        # parent/child relationship, so rendering it as flat siblings was discarding
        # real structure. See notes/pipeline_upgrade_plan.md item 3 (low-hanging
        # consolidation): nesting only what the graph already says, no curation.
        direct_subpages = collect_subpages_recursive(t, delay_s=effective_subpage_delay, seen_titles=seen_titles)
        pages.append(PageSkeleton(title=t, pageid=pid_int, subpages=direct_subpages))

    sub_skeletons.sort(key=lambda x: x.title)
    pages.sort(key=lambda x: x.title)

    return CatSkeleton(
        title=title,
        full_title=full_cat_title,
        id=this_id,
        subcats=sub_skeletons,
        pages=pages,
    )


def page_id(title: str, owning_cat_id: Optional[str] = None) -> str:
    """Page node id. `owning_cat_id` (the CatSkeleton.id -- already unique and
    path-derived, see cat_id_for_path) scopes the id to where the page was
    reached from.

    Necessary because MediaWiki page titles are NOT reliably unique as tree
    positions: a page that's a member of more than one category (see
    CLAUDE.md's category-pointer / multi-parented-categories section, and
    attach_stats' page-dedup rollup) legitimately appears as a top-level
    page under two different categories, with the exact same bare title --
    confirmed live (e.g. जयाख्यसंहिता under both root and आगमः/पाञ्चरात्रागमः,
    155 such top-level collisions as of the 2026-07 crawl). A bare
    f"page:{title}" id collides across those occurrences; since expand/
    collapse UI state (app.js's expandedSubpages/expandedPageLists) is keyed
    by node id, a bare-title collision made toggling one occurrence's nested
    subpages open also silently open the other, unrelated occurrence's --
    found via the nested-subpage-rendering feature, which was the first
    thing to actually key persistent UI state off a page id. owning_cat_id=None
    (the OCR bucket's ocr-page leaves, which have no owning category at all)
    falls back to the old bare-title id -- collisions there are structurally
    impossible since each Index: work's scans are unique to it."""
    if owning_cat_id:
        return f"page:{owning_cat_id}::{title}"
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


def page_skeleton_to_json(page: PageSkeleton, owning_cat_id: str) -> dict:
    """Build the raw JSON for a single page, recursively including its own
    MediaWiki subpages (if any) as a nested `subpages` array -- see
    PageSkeleton/collect_subpages_recursive for why this is a tree, not a
    flat list. Same node shape at every depth, so the frontend can render
    subpages-of-subpages with the same recursive logic.

    `owning_cat_id` is the enclosing category's id, threaded down unchanged
    through recursive subpage calls -- see page_id() for why this scoping is
    necessary (bare page titles collide across a page's multiple category
    occurrences).

    `stats.last_changed` is a max() rollup over this page's own revision
    timestamp AND every descendant subpage's, NOT just this page's own
    rvprop=timestamp -- deliberately, per CLAUDE.md's "index-page timestamps
    are unreliable" finding (confirmed live: नैषधीयचरितम्'s index page sat at
    2015 while a real child सर्गः was last edited 2024, a 9-year-stale gap).
    That finding drove category-level last_changed to already be a rollup;
    now that subpages collapse under their parent page by default in the UI
    (see app.js renderPageLi), the parent's own displayed date must be the
    same kind of rollup, or a collapsed view would visually understate
    freshness by showing only the parent's often-stale own edit date. The
    page's own un-rolled-up timestamp is preserved separately as
    `own_last_changed`, in case it's ever needed.

    `stats.bytes`/`content_bytes_est`/`iast_bytes_est` are likewise sum()
    rollups over this page's own size plus every descendant subpage's --
    same reasoning as the date rollup, just sum instead of max. Without this,
    an index/ToC page like नैषधीयचरितम्‌ (2.1KB of its own text, but pointing
    at 23 सर्गः subpages totaling >1MB of real content) would show a
    misleadingly tiny size when collapsed, understating the work's real size
    by three orders of magnitude. The page's own un-rolled-up size is
    preserved separately as `own_bytes`, for the same reason own_last_changed
    is kept (e.g. so the UI can label the index page's own contribution when
    expanded, distinct from the collection total shown collapsed)."""
    b = _size_cache.get(page.pageid, 0)
    own_ts = _timestamp_cache.get(page.pageid, "")
    own_content_bytes = estimate_content_bytes(b)
    subpages_json = [page_skeleton_to_json(sp, owning_cat_id) for sp in page.subpages]
    rolled_up_ts = max(
        [own_ts] + [sp["stats"]["last_changed"] for sp in subpages_json],
        default="",
    )
    rolled_up_bytes = b + sum(sp["stats"]["bytes"] for sp in subpages_json)
    rolled_up_content_bytes = own_content_bytes + sum(sp["stats"]["content_bytes_est"] for sp in subpages_json)
    return {
        "id": page_id(page.title, owning_cat_id),
        "type": "page",
        "title": page.title,
        "url": page_url(page.title),
        "stats": {
            "bytes": rolled_up_bytes,
            "content_bytes_est": rolled_up_content_bytes,
            "iast_bytes_est": estimate_iast_bytes(rolled_up_content_bytes),
            "last_changed": rolled_up_ts,
            "own_last_changed": own_ts,
            "own_bytes": b,
            "own_content_bytes_est": own_content_bytes,
            "own_iast_bytes_est": estimate_iast_bytes(own_content_bytes),
        },
        "subpages": subpages_json,
    }


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
    pages_json = [page_skeleton_to_json(p, node.id) for p in node.pages]

    return {
        "id": node.id,
        "type": "category",
        "title": node.title,
        "children": children_json,
        "pages": pages_json,
        # Filled in afterward by merge_index_pages(), in the same post-walk
        # pass as attach_stats' category-pointer dedup -- see that function
        # for why a same-titled page/subcategory collision (e.g.
        # महाकाव्यम्/किरातार्जुनीयम्) is resolved there instead of during the
        # walk itself.
        "index_page": None,
    }


def merge_index_pages(root: dict) -> None:
    """
    Resolves a second kind of graph redundancy, alongside category-pointer
    dedup (both run in this same post-walk pass, over the already-built JSON
    tree, deliberately -- see build_skeleton, which does pure discovery and no
    merge logic of its own): a category can hold a set of pages that are ALSO
    exactly the MediaWiki subpages of a same-titled sibling page under the
    same parent. Confirmed live: महाकाव्यम् holds both a subcategory
    वर्गः:किरातार्जुनीयम् (18 सर्गः as direct categorymembers) AND a same-named
    page किरातार्जुनीयम् whose own subpages are those SAME 18 सर्गः (identical
    urls/bytes per title) -- two independent discovery paths
    (categorymembers vs. subpage-title-prefix matching) landing on the same
    underlying wiki pages. Unlike category-pointer (which only dedupes when
    the same CATEGORY is filed under two parents), this is a category
    colliding with a page by title, which seen_cats never catches.

    For every category node with a sibling page of the identical title, the
    page is removed from `pages` and attached as the category's `index_page`
    instead -- a link to the page that organizes those members, not a second
    copy of them.

    Earlier this only fired if the page's own subpages were already a subset
    of the category's *other* direct page members -- but confirmed live on
    कुमारसम्भवम्: the category's only direct members were the index page
    itself (`kumārasambhavam्` with anusvāra spelling, ंभ) plus one unrelated
    loose सर्ग (`kumārasambhavam्/प्रथमः सर्गः`, filed under the OTHER spelling,
    म्भ) -- the subset check compared the index page's 6 ंभ-spelled subpages
    against a set that never contained them (just the index page's own title
    and the unrelated सर्ग), so it never merged. There is no content reason to
    gate the merge on that check: the index page's own subpages are real
    content that attach_stats (below) walks and counts directly via
    `flatten_page`, independent of whatever else the category's `pages` list
    holds -- so merging can never silently drop content, whether or not the
    category happens to *also* redundantly list those same subpage titles.
    Any other loose pages under the category (like the stray प्रथमः सर्गः
    here) are simply left in `pages` untouched, alongside the new
    `index_page`.
    """

    def visit(node: dict) -> None:
        children = node.get("children", [])
        pages = node.get("pages", [])
        if children and pages:
            by_title = {ch["title"]: ch for ch in children if ch.get("type") == "category"}
            kept_pages = []
            for p in pages:
                cat = by_title.get(p["title"])
                if cat is not None:
                    cat["index_page"] = p
                    continue
                kept_pages.append(p)
            node["pages"] = kept_pages
        for ch in children:
            visit(ch)

    visit(root)


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

    def flatten_page(p: dict, out: Dict[str, Tuple[int, int, int, str]]) -> None:
        # A page's own nested subpages (see PageSkeleton/page_skeleton_to_json)
        # count toward its category's stats exactly like a flat sibling would --
        # nesting is a display-order change only, not a change in what's counted.
        # Uses own_bytes/own_*_est here (this page's size alone), NOT the
        # already-rolled-up bytes/content_bytes_est/iast_bytes_est fields --
        # those already sum in every descendant subpage (see
        # page_skeleton_to_json), and this function walks + sums the same
        # subpage tree itself one line below, so using the rolled-up fields
        # here would double-count every subpage's bytes at the category level.
        # last_changed has no such split: it's a max(), not a sum(), so
        # reusing the rolled-up value (instead of own_last_changed) here is
        # correct and just redundant with, not wrong alongside, the recursion.
        out[p["id"]] = (
            int(p["stats"].get("own_bytes", p["stats"]["bytes"]) or 0),
            int(p["stats"].get("own_content_bytes_est", p["stats"]["content_bytes_est"]) or 0),
            int(p["stats"].get("own_iast_bytes_est", p["stats"]["iast_bytes_est"]) or 0),
            str(p["stats"]["last_changed"] or ""),
        )
        for sp in p.get("subpages", []):
            flatten_page(sp, out)

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

        merged: Dict[str, Tuple[int, int, int, str]] = {}
        for p in node.get("pages", []):
            flatten_page(p, merged)
        for ch in node.get("children", []):
            merged.update(collect(ch["id"]))
        # index_page (see merge_index_pages): its own subpages are real
        # content and are NOT guaranteed to already appear among `pages`/
        # `children` above -- confirmed live on कुमारसम्भवम्, where the
        # category's only direct members were the index page itself and one
        # unrelated loose सर्ग, never the index page's own 6 subpages. So this
        # must flatten the index page like any other page (own_* size/date
        # plus every descendant subpage), not just add its own_* contribution
        # standalone. `setdefault` guards the case where a subpage title DOES
        # also happen to be separately filed as a direct category member
        # (same underlying page, reached two ways) -- first write wins,
        # nothing is double-counted either way.
        idx = node.get("index_page")
        if idx is not None:
            flattened: Dict[str, Tuple[int, int, int, str]] = {}
            flatten_page(idx, flattened)
            for pid, val in flattened.items():
                merged.setdefault(pid, val)
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


def attach_ocr_stats(ocr_root: dict) -> None:
    """Fill in `stats` (bytes/count/last_changed only -- no content_bytes_est/
    iast_bytes_est, since those are a regression fit against ordinary
    Devanagari running-text pages and don't apply to raw OCR scan text) on
    every ocr-work node and the ocr-collection root, bottom-up. No dedup pass
    needed here (unlike attach_stats for categories): each Index: work's
    Page: scans are unique to it, there's no equivalent of a category filed
    under two parents in this bucket."""
    for work in ocr_root.get("children", []):
        pages = work.get("subpages", [])
        work["stats"] = {
            "bytes": sum(int(p["stats"]["bytes"] or 0) for p in pages),
            "count": len(pages),
            "last_changed": max((str(p["stats"]["last_changed"] or "") for p in pages), default=""),
        }

    ocr_root["stats"] = {
        "bytes": sum(w["stats"]["bytes"] for w in ocr_root.get("children", [])),
        "count": sum(w["stats"]["count"] for w in ocr_root.get("children", [])),
        "last_changed": max((w["stats"]["last_changed"] for w in ocr_root.get("children", [])), default=""),
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


def sort_page_subpages(page: dict) -> None:
    """Sort a page's nested `subpages` in place by natural-sorted title, recursively --
    same treatment sort_tree gives category children/pages, applied one level down."""
    subpages = page.get("subpages")
    if subpages:
        subpages.sort(key=lambda n: _natural_sort_key(n["title"]))
        for sp in subpages:
            sort_page_subpages(sp)


def sort_tree(node: dict) -> None:
    """Sort children/pages/subpages in place by natural-sorted title, recursively.
    Runs once on the final assembled JSON dict, so it's cache-transparent -- reruns
    from a warm .api_cache/ re-sort identically regardless of crawl order. Handles
    both the category tree (children/pages) and the OCR bucket (children of
    ocr-collection are ocr-work nodes, whose leaves are `subpages` rather than
    `pages` -- see build_unpublished_ocr_bucket) with the same walk."""
    if "children" in node:
        node["children"].sort(key=lambda n: _natural_sort_key(n["title"]))
        for ch in node["children"]:
            sort_tree(ch)
    if "pages" in node:
        node["pages"].sort(key=lambda n: _natural_sort_key(n["title"]))
        for p in node["pages"]:
            sort_page_subpages(p)
    if "subpages" in node:
        sort_page_subpages(node)
    if node.get("index_page") is not None:
        sort_page_subpages(node["index_page"])


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

    progress = CategoryProgress(expected_total=expected_category_count(args.out))
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
    # Must run before attach_stats: merge_index_pages moves a page into its
    # colliding sibling category's index_page field, and attach_stats reads
    # index_page while computing that category's rolled-up stats.
    merge_index_pages(out["root"])
    attach_stats(out["root"])
    sort_tree(out["root"])

    # Unpublished-OCR bucket: swept separately from the category walk above,
    # since Index:/Page: (ns 106/104) scans aren't reachable via categorymembers
    # at all -- this is item 5(d) of notes/pipeline_upgrade_plan.md. Attached as
    # a sibling of the category children *after* attach_stats/sort_tree run on
    # them, since its node shapes (ocr-collection/ocr-work/ocr-page) don't match
    # what attach_stats expects (content_bytes_est/iast_bytes_est don't apply to
    # raw OCR text) -- see attach_ocr_stats for why it's computed separately.
    print("Sweeping unpublished OCR works (Index:/Page: namespaces) ...")
    ocr_status = SimpleStatusLine()
    activity.bind(ocr_status)
    ocr_bucket = build_unpublished_ocr_bucket(
        delay_s=args.subpage_delay if args.subpage_delay is not None else args.delay,
        progress=ocr_status,
    )
    ocr_status.close()
    activity.bind(None)
    if ocr_bucket:
        attach_ocr_stats(ocr_bucket)
        sort_tree(ocr_bucket)
        out["root"]["children"].append(ocr_bucket)

    # Orphaned-content bucket: mainspace pages unreachable from ग्रन्थाः/
    # धर्मशास्त्रम् by any depth of category/subpage descent -- either no
    # category tag at all, or a category tag pointing to a category that is
    # itself never filed under any parent (item 5(b)/(c) of
    # notes/pipeline_upgrade_plan.md's 2026-07-11 gap audit). Swept last,
    # after seen_cats reflects the full main crawl (including OCR bucket's
    # sibling categories, if any -- though OCR pages live in ns 104/106 and
    # can never collide with a mainspace title anyway), so "orphaned" means
    # "genuinely unreached by everything above", not a stale mid-crawl snapshot.
    print("Sweeping orphaned mainspace content (uncategorized + orphaned-category pages) ...")
    placed_titles: Set[str] = set()
    collect_placed_titles(out["root"], placed_titles)
    if ocr_bucket:
        collect_placed_titles(ocr_bucket, placed_titles)

    orphan_status = SimpleStatusLine()
    activity.bind(orphan_status)
    orphan_bucket = build_orphan_bucket(
        delay_s=args.delay,
        subpage_delay_s=args.subpage_delay if args.subpage_delay is not None else args.delay,
        seen_cats=seen_cats,
        placed_titles=placed_titles,
        progress=orphan_status,
    )
    orphan_status.close()
    activity.bind(None)
    if orphan_bucket:
        merge_index_pages(orphan_bucket)
        attach_stats(orphan_bucket)
        sort_tree(orphan_bucket)
        out["root"]["children"].append(orphan_bucket)

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
