import argparse
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
from tqdm.auto import tqdm

BASE_URL = "https://sa.wikisource.org"
API_URL = f"{BASE_URL}/w/api.php"

log = logging.getLogger("scrape")

GRANTHAH_CAT = "वर्गः:ग्रन्थाः"
DHARMASHASTRA_CAT = "वर्गः:धर्मशास्त्रम्"

DEFAULT_OUT = "docs/data/tree.json"

REQUEST_DELAY_S = 0.5
TIMEOUT_S = 30
MAX_RETRIES = 3

# Only probe a page for MediaWiki subpages if its own size is below this
# threshold. Real content pages (verse text etc.) run tens to hundreds of KB;
# only small index/ToC pages point to further subpages in practice (both
# known cases for नैषधीयचरितम् are well under 5KB: 433B and 2.1KB). Without
# this filter, probing every single page for subpages roughly doubles total
# request volume and reliably trips Wikimedia's edge rate limiter.
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
    the bottom via carriage return, showing count vs. EXPECTED_CATEGORY_COUNT.
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
        line = f"{self.count}/{self.expected_total} categories ({pct:.0f}%)"
        self._status_len = len(line)
        sys.stdout.write(line)
        sys.stdout.flush()

    def visit(self, title: str, depth: int) -> None:
        self.count += 1
        self._clear_status()
        print(f"{'    ' * depth}{title}")
        self._write_status()

    def close(self) -> None:
        self._clear_status()
        self._write_status()
        sys.stdout.write("\n")
        sys.stdout.flush()

# Disk-backed cache of raw API responses, keyed by a hash of the request params.
# Survives across runs so re-running the scraper (e.g. during dev iteration, or
# after a rate-limit interruption) doesn't have to re-hit the live API for
# requests already answered. Delete this directory to force a fresh crawl.
API_CACHE_DIR = Path(__file__).parent / ".api_cache"


session = requests.Session()
session.headers.update(
    {"User-Agent": "WikisourceCategoryCrawler/3.0 (polite; single-threaded; research use)"}
)

_size_cache: Dict[int, int] = {}          # pageid -> bytes
_timestamp_cache: Dict[int, str] = {}     # pageid -> last-revision ISO 8601 timestamp
_api_cache: Dict[str, dict] = {}          # small memo for identical API calls


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


def api_get(params: dict, delay_s: float) -> dict:
    # maxlag: standard MediaWiki bot-etiquette signal for non-interactive tasks.
    # See https://www.mediawiki.org/wiki/API:Etiquette
    params = {**params, "maxlag": "5"}

    key = json.dumps(params, sort_keys=True, ensure_ascii=False)
    if key in _api_cache:
        log.debug("MEM-CACHE HIT  %s", _short_params(params))
        return _api_cache[key]

    cache_path = _cache_path_for_key(key)
    if cache_path.exists():
        log.debug("DISK-CACHE HIT %s", _short_params(params))
        with cache_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _api_cache[key] = data
        return data

    log.debug("NETWORK CALL   %s", _short_params(params))
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
                time.sleep(wait_s)
            else:
                log.debug("  -> error on attempt %d: %r, sleeping %.1fs", attempt, e, delay_s * attempt)
                time.sleep(delay_s * attempt)

    raise RuntimeError(f"API request failed after {MAX_RETRIES} retries.\nParams: {params}\n{last_err}")


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


def count_disk_cached_pages(pageids: List[int]) -> int:
    """
    Replays fetch_page_meta's batching to count how many pages are already
    resolvable from the on-disk API cache, without making any requests. Used
    only to annotate the phase-2 header with how much of the bar's initial
    burst will be instant cache hits vs. real network calls — the tqdm bar
    itself still starts at 0 and counts every batch fetch_page_meta processes,
    cached or not, so this count is informational only, not passed to tqdm.
    """
    BATCH = 50
    cached = 0
    for i in range(0, len(pageids), BATCH):
        batch = pageids[i : i + BATCH]
        params = {**_page_meta_params(batch), "maxlag": "5"}  # matches api_get's own merge
        key = json.dumps(params, sort_keys=True, ensure_ascii=False)
        if _cache_path_for_key(key).exists():
            cached += len(batch)
    return cached


def fetch_page_meta(pageids: List[int], delay_s: float, pbar: Optional[tqdm]) -> None:
    """
    Populate _size_cache and _timestamp_cache for each pageid,
    using prop=revisions&rvprop=size|timestamp (batch).
    """
    BATCH = 50
    for i in range(0, len(pageids), BATCH):
        batch = pageids[i : i + BATCH]
        missing = [pid for pid in batch if pid not in _size_cache]
        if not missing:
            if pbar is not None:
                pbar.update(len(batch))
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

        if pbar is not None:
            pbar.update(len(batch))


def fetch_subpages(title: str, delay_s: float) -> List[dict]:
    """
    Returns direct MediaWiki subpages of `title` (namespace 0), i.e. pages
    whose title starts with "<title>/". One level only; does not recurse.
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
    """
    found: List[Tuple[str, int]] = []
    direct = fetch_subpages(title, delay_s=delay_s)

    new_pids = [
        int(m["pageid"])
        for m in direct
        if m.get("title") and m.get("pageid") is not None and m["title"] not in seen_titles
    ]
    fetch_page_meta(new_pids, delay_s=delay_s, pbar=None)

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
    collect_pageids: Set[int],
    depth: int,
    path: List[str],
    progress: Optional["CategoryProgress"] = None,
    recurse_subpages: bool = False,
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
                collect_pageids=collect_pageids,
                depth=depth + 1,
                path=this_path,
                progress=progress,
                recurse_subpages=recurse_subpages,
            )
        )

    pages: List[Tuple[str, int]] = []
    seen_titles: Set[str] = {t for t, _ in pages}

    if recurse_subpages:
        # Fetch sizes up front for this batch of pages, so we can skip the (costly)
        # subpage probe for pages that are clearly real content, not index/ToC pages.
        direct_pageids = [int(m["pageid"]) for m in page_members if m.get("pageid") is not None]
        fetch_page_meta(direct_pageids, delay_s=delay_s, pbar=None)

    for m in page_members:
        t = m.get("title", "")
        pid = m.get("pageid")
        if not t or pid is None or t in seen_titles:
            continue
        pid_int = int(pid)
        seen_titles.add(t)
        pages.append((t, pid_int))
        collect_pageids.add(pid_int)

        # Recurse into MediaWiki subpages (e.g. an index page's "Title/सर्गः" children),
        # since categorymembers only sees the index page itself, not its subpages.
        # DISABLED BY DEFAULT (recurse_subpages=False): confirmed to trip Wikimedia's
        # rate limiter repeatedly on deeply-nested works (e.g. बैबल्, which nests
        # book/chapter subpages several levels deep) even with the size gate below
        # and proper request pacing. Not ripped out — re-enable via --recurse-subpages
        # once request-volume handling is more robust (see roadmap item #2 in
        # CLAUDE.md / pipeline upgrade plan).
        if recurse_subpages and _size_cache.get(pid_int, 0) <= SUBPAGE_PROBE_MAX_BYTES:
            subpages = collect_subpages_recursive(t, delay_s=delay_s, seen_titles=seen_titles)
            for sub_t, sub_pid in subpages:
                pages.append((sub_t, sub_pid))
                collect_pageids.add(sub_pid)

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
        pages_json.append(
            {
                "id": page_id(t),
                "type": "page",
                "title": t,
                "url": page_url(t),
                "stats": {"bytes": b, "last_changed": ts},
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

    # Memoized: node id -> {page_id: (bytes, last_changed)} for every distinct page
    # reachable from that node (pointer nodes resolve to their target's page set).
    memo: Dict[str, Dict[str, Tuple[int, str]]] = {}

    def collect(node_id: str) -> Dict[str, Tuple[int, str]]:
        if node_id in memo:
            return memo[node_id]
        node = by_id[node_id]
        if node["type"] == "category-pointer":
            result = collect(node["points_to"])
            memo[node_id] = result
            return result

        merged: Dict[str, Tuple[int, str]] = {
            p["id"]: (int(p["stats"]["bytes"] or 0), str(p["stats"]["last_changed"] or ""))
            for p in node.get("pages", [])
        }
        for ch in node.get("children", []):
            merged.update(collect(ch["id"]))
        memo[node_id] = merged
        return merged

    for node_id in by_id:
        pages = collect(node_id)
        by_id[node_id]["stats"] = {
            "bytes": sum(b for b, _ in pages.values()),
            "count": len(pages),
            "last_changed": max((ts for _, ts in pages.values()), default=""),
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY_S)
    ap.add_argument(
        "--page-meta-delay",
        type=float,
        default=None,
        help="Delay (seconds) between phase-2 prop=revisions batch requests, separate from "
        "--delay. Phase 2 issues many same-shaped requests back-to-back and has been observed "
        "tripping Wikimedia's edge rate limiter after ~10 requests at the default --delay pace, "
        "each trip costing a ~55s Retry-After stall (in one observed run, over 90%% of phase-2 "
        "wall-clock time was spent waiting out these stalls). No safe steady-state pace has been "
        "empirically confirmed yet -- tune this by testing live against the real API. Defaults "
        "to --delay if unset.",
    )
    ap.add_argument("--debug", action="store_true", help="log every API call (cache hit/miss, timing, retries) to stderr")
    ap.add_argument(
        "--recurse-subpages",
        action="store_true",
        help="EXPERIMENTAL, off by default: also discover MediaWiki subpages "
        "(Title/Subtitle) via list=allpages. Confirmed to trip Wikimedia's rate "
        "limiter repeatedly on deeply-nested works (e.g. बैबल्); see CLAUDE.md.",
    )
    args = ap.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Pass 1: build full category skeleton starting at ग्रन्थाः (but we will not output the top ग्रन्थाः node)
    print("Phase 1/2: walking category tree (live, depth-first) ...")
    seen_cats: Dict[str, str] = {}
    all_pageids: Set[int] = set()

    progress = CategoryProgress()

    granth_skel = build_skeleton(
        GRANTHAH_CAT,
        delay_s=args.delay,
        seen_cats=seen_cats,
        collect_pageids=all_pageids,
        depth=0,
        path=[],
        progress=progress,
        recurse_subpages=args.recurse_subpages,
    )

    # Inject धर्मशास्त्रम् as an extra top-level child under ग्रन्थाः (as requested),
    # even if it isn't actually listed as a subcategory there.
    has_dharma = any(ch.title == strip_cat_prefix(DHARMASHASTRA_CAT) for ch in granth_skel.subcats)
    if not has_dharma:
        dh_seen: Dict[str, str] = dict(seen_cats)
        dh_all: Set[int] = set(all_pageids)
        dh_skel = build_skeleton(
            DHARMASHASTRA_CAT,
            delay_s=args.delay,
            seen_cats=dh_seen,
            collect_pageids=dh_all,
            depth=1,
            path=[],
            progress=progress,
            recurse_subpages=args.recurse_subpages,
        )
        # Merge seen/pageid sets (safe; categories might overlap)
        seen_cats = dh_seen
        all_pageids = dh_all
        granth_skel.subcats.append(dh_skel)

    progress.close()

    granth_skel.subcats.sort(key=lambda x: x.title)

    # Pass 2: fetch sizes and timestamps for unique pages (fast, no HTML fetching)
    unique_pageids = sorted(all_pageids)
    already_cached = count_disk_cached_pages(unique_pageids)
    print(
        f"Phase 2/2: fetching page sizes and timestamps for {len(unique_pageids)} unique pages "
        f"({already_cached} already cached on disk) ..."
    )
    page_meta_delay = args.page_meta_delay if args.page_meta_delay is not None else args.delay
    pbar = tqdm(total=len(unique_pageids), unit="page")
    fetch_page_meta(unique_pageids, delay_s=page_meta_delay, pbar=pbar)
    pbar.close()

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
