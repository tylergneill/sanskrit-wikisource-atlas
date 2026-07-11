"""One-off script that produced changelog entry #2.

Compares main (dedup-recomputed, clean) vs current branch tip
(dedup-correct) to isolate pure live-data growth: both sides deduped, no
bug-fix noise. This is a one-off, not a compare.py run, because the "old"
side isn't a real file's root.stats (main's own root.stats is buggy) --
it's the recomputed baseline established by
notes/oneoff_main_dedup_audit.py / changelog entry #1. See
notes/changelog_plan.md for the full rationale.

Kept here for the record, not meant to be re-run routinely.

Usage (from repo root):
    git show main:docs/data/tree.json > /tmp/tree_main.json
    python notes/oneoff_main_clean_vs_tip.py /tmp/tree_main.json docs/data/tree.json
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "docs" / "data" / "changelog.json"


def collect_pages(root):
    pages = {}

    def walk(node):
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


def main():
    if len(sys.argv) != 3:
        print("usage: python notes/oneoff_main_clean_vs_tip.py <path to main's tree.json> <path to current tree.json>")
        sys.exit(1)
    main_tree_path = Path(sys.argv[1])
    current_tree_path = Path(sys.argv[2])

    main_data = json.loads(main_tree_path.read_text())
    current_data = json.loads(current_tree_path.read_text())

    main_pages = collect_pages(main_data["root"])
    current_pages = collect_pages(current_data["root"])

    old_bytes = sum(p.get("bytes", 0) or 0 for p in main_pages.values())
    old_count = len(main_pages)

    new_stats = current_data["root"]["stats"]
    new_bytes = new_stats["bytes"]
    new_count = new_stats["count"]

    added = sorted(set(current_pages) - set(main_pages))
    removed = sorted(set(main_pages) - set(current_pages))

    delta_bytes = new_bytes - old_bytes
    delta_count = new_count - old_count

    log = json.loads(CHANGELOG.read_text()) if CHANGELOG.exists() else []
    next_id = max((e.get("id", 0) for e in log), default=0) + 1

    entry = {
        "id": next_id,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": "live-data growth only, clean-to-clean (main dedup-recomputed vs current branch tip)",
        "notes": (
            "One-off, not a normal pipeline comparison. 'old' is main's dedup-recomputed baseline "
            "from the previous changelog entry (259,163,815 bytes / 4,522 pages), not main's own "
            "(buggy) root.stats. 'new' is the current branch tip's root.stats, already dedup-correct. "
            "Both sides are deduped, so this delta isolates pure live-Wikisource-data growth (new/edited "
            "pages) between the main snapshot and today, with no bug-fix effect mixed in -- that effect "
            "was isolated separately in the previous entry. Page-level last-edited-date comparison is "
            "not possible against main: that snapshot predates the last_changed field entirely, so "
            "'0 pages with a changed timestamp' below reflects a schema gap, not a confirmed absence of "
            "edits. Page added/removed counts ARE meaningful (both sides have page ids)."
        ),
        "old": {"bytes": old_bytes, "count": old_count, "last_changed": None},
        "new": {"bytes": new_bytes, "count": new_count, "last_changed": new_stats.get("last_changed")},
        "delta": {
            "bytes": delta_bytes,
            "bytes_pct": 100.0 * delta_bytes / old_bytes if old_bytes else None,
            "count": delta_count,
            "count_pct": 100.0 * delta_count / old_count if old_count else None,
        },
        "pages_added": added,
        "pages_removed": removed,
        "pages_with_changed_timestamp": [],
    }

    print(f"old: bytes={old_bytes:,} count={old_count:,}")
    print(f"new: bytes={new_bytes:,} count={new_count:,}")
    print(f"delta: bytes={delta_bytes:+,} ({100.0*delta_bytes/old_bytes:+.1f}%) count={delta_count:+,} ({100.0*delta_count/old_count:+.1f}%)")
    print(f"pages added: {len(added)}  pages removed: {len(removed)}")
    print()
    print("This script does not write the changelog automatically (avoids accidental")
    print("duplicate entries on rerun). To append, uncomment the lines below.")
    # log.append(entry)
    # CHANGELOG.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
    # print(f"appended entry to {CHANGELOG}")


if __name__ == "__main__":
    main()
