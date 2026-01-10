import argparse
import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import requests
from tqdm.auto import tqdm

BASE_URL = "https://sa.wikisource.org"
API_URL = f"{BASE_URL}/w/api.php"

GRANTHAH_CAT = "वर्गः:ग्रन्थाः"
DHARMASHASTRA_CAT = "वर्गः:धर्मशास्त्रम्"

DEFAULT_OUT = "docs/data/tree.json"

REQUEST_DELAY_S = 0.5
TIMEOUT_S = 30
MAX_RETRIES = 3


session = requests.Session()
session.headers.update(
    {"User-Agent": "WikisourceCategoryCrawler/3.0 (polite; single-threaded; research use)"}
)

_size_cache: Dict[int, int] = {}      # pageid -> bytes
_cat_size_cache: Dict[str, int] = {}  # full_title -> bytes
_cat_count_cache: Dict[str, int] = {} # full_title -> page_count
_api_cache: Dict[str, dict] = {}      # small memo for identical API calls


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


def api_get(params: dict, delay_s: float) -> dict:
    key = json.dumps(params, sort_keys=True, ensure_ascii=False)
    if key in _api_cache:
        return _api_cache[key]

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(delay_s)
            r = session.get(API_URL, params=params, timeout=TIMEOUT_S)
            r.raise_for_status()
            data = r.json()
            _api_cache[key] = data
            return data
        except Exception as e:
            last_err = e
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


def fetch_sizes(pageids: List[int], delay_s: float, pbar: Optional[tqdm]) -> None:
    """
    Populate _size_cache for each pageid, using prop=revisions&rvprop=size (batch).
    """
    BATCH = 50
    for i in range(0, len(pageids), BATCH):
        batch = pageids[i : i + BATCH]
        missing = [pid for pid in batch if pid not in _size_cache]
        if not missing:
            if pbar is not None:
                pbar.update(len(batch))
            continue

        params = {
            "action": "query",
            "format": "json",
            "prop": "revisions",
            "rvprop": "size",
            "pageids": "|".join(str(pid) for pid in missing),
        }
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

        if pbar is not None:
            pbar.update(len(batch))


@dataclass
class CatSkeleton:
    title: str              # no "वर्गः:" prefix
    full_title: str         # full MediaWiki title (with namespace)
    subcats: List["CatSkeleton"]
    pages: List[Tuple[str, int]]   # (page_title, pageid)


def build_skeleton(
    full_cat_title: str,
    delay_s: float,
    seen_cats: Set[str],
    collect_pageids: Set[int],
    verbose: bool,
    depth: int,
) -> CatSkeleton:
    if full_cat_title in seen_cats:
        return CatSkeleton(
            title=strip_cat_prefix(full_cat_title),
            full_title=full_cat_title,
            subcats=[],
            pages=[],
        )
    seen_cats.add(full_cat_title)

    if verbose:
        indent = " " * 4 * depth
        print(f"{indent}{strip_cat_prefix(full_cat_title)}")

    subcat_members, page_members = category_members(full_cat_title, delay_s=delay_s)

    sub_skeletons: List[CatSkeleton] = []
    for m in subcat_members:
        sub_full = m.get("title", "")
        if not sub_full:
            continue
        
        # Exclude internal/maintenance categories
        stripped = strip_cat_prefix(sub_full)
        if stripped in ["अवैध एचटीएमएल टैग का उपयोग कर रहे पृष्ठ", "अनिर्दिष्टानि पुटानि"]:
            continue

        sub_skeletons.append(
            build_skeleton(
                sub_full,
                delay_s=delay_s,
                seen_cats=seen_cats,
                collect_pageids=collect_pageids,
                verbose=verbose,
                depth=depth + 1,
            )
        )

    pages: List[Tuple[str, int]] = []
    for m in page_members:
        t = m.get("title", "")
        pid = m.get("pageid")
        if not t or pid is None:
            continue
        pid_int = int(pid)
        pages.append((t, pid_int))
        collect_pageids.add(pid_int)

    sub_skeletons.sort(key=lambda x: x.title)
    pages.sort(key=lambda x: x[0])

    return CatSkeleton(
        title=strip_cat_prefix(full_cat_title),
        full_title=full_cat_title,
        subcats=sub_skeletons,
        pages=pages,
    )


def cat_id(title: str) -> str:
    return f"cat:{title}"


def page_id(title: str) -> str:
    return f"page:{title}"


def skeleton_to_json(node: CatSkeleton) -> dict:
    children_json = [skeleton_to_json(ch) for ch in node.subcats]

    pages_json = []
    pages_bytes = 0
    for t, pid in node.pages:
        b = _size_cache.get(pid, 0)
        pages_bytes += b
        pages_json.append(
            {
                "id": page_id(t),
                "type": "page",
                "title": t,
                "url": page_url(t),
                "stats": {"bytes": b},
            }
        )

    children_bytes = sum(int(ch.get("stats", {}).get("bytes", 0)) for ch in children_json)
    total_bytes = pages_bytes + children_bytes

    children_count = sum(int(ch.get("stats", {}).get("count", 0)) for ch in children_json)
    total_count = len(pages_json) + children_count

    # Cache the result for full nodes, and reuse it for empty nodes (broken cycles/repeats)
    if node.subcats or node.pages:
        _cat_size_cache[node.full_title] = total_bytes
        _cat_count_cache[node.full_title] = total_count
    elif node.full_title in _cat_size_cache:
        total_bytes = _cat_size_cache[node.full_title]
        total_count = _cat_count_cache.get(node.full_title, 0)

    return {
        "id": cat_id(node.title),
        "type": "category",
        "title": node.title,
        "children": children_json,
        "pages": pages_json,
        "stats": {"bytes": total_bytes, "count": total_count},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY_S)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # Pass 1: build full category skeleton starting at ग्रन्थाः (but we will not output the top ग्रन्थाः node)
    seen_cats: Set[str] = set()
    all_pageids: Set[int] = set()

    granth_skel = build_skeleton(
        GRANTHAH_CAT,
        delay_s=args.delay,
        seen_cats=seen_cats,
        collect_pageids=all_pageids,
        verbose=args.verbose,
        depth=0,
    )

    # Inject धर्मशास्त्रम् as an extra top-level child under ग्रन्थाः (as requested),
    # even if it isn't actually listed as a subcategory there.
    has_dharma = any(ch.title == strip_cat_prefix(DHARMASHASTRA_CAT) for ch in granth_skel.subcats)
    if not has_dharma:
        dh_seen: Set[str] = set(seen_cats)
        dh_all: Set[int] = set(all_pageids)
        dh_skel = build_skeleton(
            DHARMASHASTRA_CAT,
            delay_s=args.delay,
            seen_cats=dh_seen,
            collect_pageids=dh_all,
            verbose=args.verbose,
            depth=1,
        )
        # Merge seen/pageid sets (safe; categories might overlap)
        seen_cats = dh_seen
        all_pageids = dh_all
        granth_skel.subcats.append(dh_skel)

    granth_skel.subcats.sort(key=lambda x: x.title)

    # Pass 2: fetch sizes for unique pages (fast, no HTML fetching)
    unique_pageids = sorted(all_pageids)
    pbar = tqdm(total=len(unique_pageids), unit="page")
    fetch_sizes(unique_pageids, delay_s=args.delay, pbar=pbar)
    pbar.close()

    # Output: omit the top "ग्रन्थाः" level, and omit "वर्गः:" prefixes everywhere (already stripped)
    # We use skeleton_to_json on the root node to get its full recursive size, then extract what we need.
    root_data = skeleton_to_json(granth_skel)

    out = {
        "root": {
            "id": "root",
            "title": "ग्रन्थाः (धर्मशास्त्राणि च)",
            "type": "collection",
            "children": root_data["children"],
            "pages": root_data["pages"],
            "stats": root_data["stats"],
        }
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    main()
