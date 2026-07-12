"""One-off script that produced changelog entry #1.

Audits main's tree.json under the current dedup-by-page-id method. main
predates the multi-parent-category dedup fix, so its own root.stats
double-counts pages filed under 2+ categories. This is NOT something
compare.py should handle going forward (every file from here on already
computes root.stats correctly) -- this script exists only to document the
bug-fix effect in isolation, on otherwise-unchanged data. See
notes/changelog_plan.md for the full rationale.

Kept here for the record, not meant to be re-run routinely -- rerunning
would just re-derive the same historical numbers and (if --append were
used again) duplicate the changelog entry.

Usage (from repo root):
    git show main:docs/data/tree.json > /tmp/tree_main.json
    python notes/oneoff_main_dedup_audit.py /tmp/tree_main.json
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
        for child in node.get("children", []):
            walk(child)
        for page in node.get("pages", []):
            walk(page)

    walk(root)
    return pages


def main():
    if len(sys.argv) != 2:
        print("usage: python notes/oneoff_main_dedup_audit.py <path to main's tree.json>")
        sys.exit(1)
    main_tree_path = Path(sys.argv[1])

    data = json.loads(main_tree_path.read_text())
    reported = data["root"]["stats"]

    pages = collect_pages(data["root"])
    recomputed_bytes = sum(p.get("bytes", 0) or 0 for p in pages.values())
    recomputed_count = len(pages)

    old = {"bytes": reported["bytes"], "count": reported["count"], "last_changed": reported.get("last_changed")}
    new = {"bytes": recomputed_bytes, "count": recomputed_count, "last_changed": None}
    delta_bytes = new["bytes"] - old["bytes"]
    delta_count = new["count"] - old["count"]

    log = json.loads(CHANGELOG.read_text()) if CHANGELOG.exists() else []
    next_id = max((e.get("id", 0) for e in log), default=0) + 1

    entry = {
        "id": next_id,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": "bug-fix effect only, on unchanged data (main, self-reported vs main, dedup-recomputed)",
        "notes": (
            "One-off audit, not a normal pipeline comparison. main's tree.json predates the "
            "multi-parent-category dedup fix, so its own reported root.stats double-counted "
            "130 pages that were already filed under 2+ categories. This entry compares main's "
            "original self-reported stats ('old') against the same main data with today's "
            "dedup-by-page-id logic manually applied ('new') -- no live-data change at all, "
            "isolating the pure effect of correcting the counting bug. From the next changelog "
            "entry onward, root.stats is always dedup-correct at generation time, so this kind "
            "of manual audit is never needed again."
        ),
        "old": old,
        "new": new,
        "delta": {
            "bytes": delta_bytes,
            "bytes_pct": 100.0 * delta_bytes / old["bytes"] if old["bytes"] else None,
            "count": delta_count,
            "count_pct": 100.0 * delta_count / old["count"] if old["count"] else None,
        },
        "pages_added": [],
        "pages_removed": [],
        "pages_with_changed_timestamp": [],
    }

    print(f"old: bytes={old['bytes']:,} count={old['count']:,}")
    print(f"new: bytes={new['bytes']:,} count={new['count']:,}")
    print(f"delta: bytes={delta_bytes:+,} count={delta_count:+,}")
    print()
    print("This script does not write the changelog automatically (avoids accidental")
    print("duplicate entries on rerun). To append, uncomment the lines below.")
    # log.append(entry)
    # CHANGELOG.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
    # print(f"appended entry to {CHANGELOG}")


if __name__ == "__main__":
    main()
