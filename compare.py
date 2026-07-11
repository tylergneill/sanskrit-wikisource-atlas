"""Compare two tree.json snapshots' root.stats, plus page-level detail.

Trusts each file's root.stats as-is: current scrape.py's attach_stats()
already dedupes by page id when computing it, so on any file produced by
the current pipeline root.stats is correct by construction. (This tool
does not handle pre-dedup-fix data like the original main branch snapshot
-- that required a one-off audit script, not this tool.)

Usage:
    python compare.py OLD.json NEW.json
    python compare.py OLD.json NEW.json --append --label "..." --notes "..."
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple


def collect_pages(root: dict) -> Dict[str, dict]:
    """Walk a tree.json root, return {page_id: stats} deduped by first occurrence."""
    pages: Dict[str, dict] = {}

    def walk(node: dict) -> None:
        if node["type"] == "page":
            if node["id"] not in pages:
                pages[node["id"]] = node["stats"]
            return
        if node["type"] == "category-pointer":
            return
        for child in node.get("children", []):
            walk(child)
        for page in node.get("pages", []):
            walk(page)

    walk(root)
    return pages


def pct(delta: float, base: float):
    if base == 0:
        return None
    return 100.0 * delta / base


def diff_timestamps(old_pages: Dict[str, dict], new_pages: Dict[str, dict]) -> list:
    """Pages present in both snapshots whose last_changed differs."""
    changed = []
    for pid in sorted(set(old_pages) & set(new_pages)):
        old_ts = old_pages[pid].get("last_changed")
        new_ts = new_pages[pid].get("last_changed")
        if old_ts and new_ts and old_ts != new_ts:
            changed.append({"id": pid, "old": old_ts, "new": new_ts})
    return changed


def added_removed(old_pages: Dict[str, dict], new_pages: Dict[str, dict]) -> Tuple[list, list]:
    added = sorted(set(new_pages) - set(old_pages))
    removed = sorted(set(old_pages) - set(new_pages))
    return added, removed


def build_report(old_path: Path, new_path: Path) -> dict:
    old_data = json.loads(old_path.read_text())
    new_data = json.loads(new_path.read_text())

    old_stats = old_data["root"].get("stats", {})
    new_stats = new_data["root"].get("stats", {})

    old_pages = collect_pages(old_data["root"])
    new_pages = collect_pages(new_data["root"])

    changed_ts = diff_timestamps(old_pages, new_pages)
    added, removed = added_removed(old_pages, new_pages)

    old_bytes = old_stats.get("bytes", 0) or 0
    new_bytes = new_stats.get("bytes", 0) or 0
    old_content_bytes = old_stats.get("content_bytes_est", 0) or 0
    new_content_bytes = new_stats.get("content_bytes_est", 0) or 0
    old_iast_bytes = old_stats.get("iast_bytes_est", 0) or 0
    new_iast_bytes = new_stats.get("iast_bytes_est", 0) or 0
    old_count = old_stats.get("count", 0) or 0
    new_count = new_stats.get("count", 0) or 0
    delta_bytes = new_bytes - old_bytes
    delta_content_bytes = new_content_bytes - old_content_bytes
    delta_iast_bytes = new_iast_bytes - old_iast_bytes
    delta_count = new_count - old_count

    return {
        "old": {
            "bytes": old_stats.get("bytes"),
            "content_bytes_est": old_stats.get("content_bytes_est"),
            "iast_bytes_est": old_stats.get("iast_bytes_est"),
            "count": old_stats.get("count"),
            "last_changed": old_stats.get("last_changed"),
        },
        "new": {
            "bytes": new_stats.get("bytes"),
            "content_bytes_est": new_stats.get("content_bytes_est"),
            "iast_bytes_est": new_stats.get("iast_bytes_est"),
            "count": new_stats.get("count"),
            "last_changed": new_stats.get("last_changed"),
        },
        "delta": {
            "bytes": delta_bytes,
            "bytes_pct": pct(delta_bytes, old_bytes),
            "content_bytes_est": delta_content_bytes,
            "content_bytes_est_pct": pct(delta_content_bytes, old_content_bytes),
            "iast_bytes_est": delta_iast_bytes,
            "iast_bytes_est_pct": pct(delta_iast_bytes, old_iast_bytes),
            "count": delta_count,
            "count_pct": pct(delta_count, old_count),
        },
        "pages_added": added,
        "pages_removed": removed,
        "pages_with_changed_timestamp": changed_ts,
    }


def print_summary(report: dict) -> None:
    o, n, d = report["old"], report["new"], report["delta"]

    def fmt_pct(v):
        return "n/a" if v is None else f"{v:+.1f}%"

    print(f"old: bytes={o['bytes']!r} content_bytes_est={o['content_bytes_est']!r} iast_bytes_est={o['iast_bytes_est']!r} count={o['count']!r} last_changed={o['last_changed']!r}")
    print(f"new: bytes={n['bytes']!r} content_bytes_est={n['content_bytes_est']!r} iast_bytes_est={n['iast_bytes_est']!r} count={n['count']!r} last_changed={n['last_changed']!r}")
    print()
    print(f"delta: bytes={d['bytes']:+,} ({fmt_pct(d['bytes_pct'])})  content_bytes_est={d['content_bytes_est']:+,} ({fmt_pct(d['content_bytes_est_pct'])})  iast_bytes_est={d['iast_bytes_est']:+,} ({fmt_pct(d['iast_bytes_est_pct'])})  count={d['count']:+,} ({fmt_pct(d['count_pct'])})")
    print()
    print(f"pages added: {len(report['pages_added'])}  pages removed: {len(report['pages_removed'])}")
    print(f"pages with changed last_changed timestamp: {len(report['pages_with_changed_timestamp'])}")
    for entry in report["pages_with_changed_timestamp"][:20]:
        print(f"  {entry['id']}: {entry['old']} -> {entry['new']}")
    if len(report["pages_with_changed_timestamp"]) > 20:
        print(f"  ... and {len(report['pages_with_changed_timestamp']) - 20} more")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", type=Path, help="older tree.json snapshot")
    ap.add_argument("new", type=Path, help="newer tree.json snapshot")
    ap.add_argument("--append", action="store_true", help="append this comparison as an entry to the changelog")
    ap.add_argument("--changelog", type=Path, default=Path("docs/data/changelog.json"), help="changelog path (default: docs/data/changelog.json)")
    ap.add_argument("--label", default="", help="short human label for this comparison, e.g. 'live-data growth, dedup-to-dedup'")
    ap.add_argument("--notes", default="", help="free-text note describing what this comparison represents")
    args = ap.parse_args()

    report = build_report(args.old, args.new)
    print_summary(report)

    if args.append:
        if args.changelog.exists():
            log = json.loads(args.changelog.read_text())
        else:
            log = []
        next_id = max((e.get("id", 0) for e in log), default=0) + 1
        entry = {
            "id": next_id,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "label": args.label,
            "notes": args.notes,
            **report,
        }
        log.append(entry)
        args.changelog.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
        print()
        print(f"appended entry to {args.changelog}")


if __name__ == "__main__":
    main()
