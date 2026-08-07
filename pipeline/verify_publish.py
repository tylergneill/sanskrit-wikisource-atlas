"""Pre-publish consistency checks for the committed contents of docs/.

Because docs/ has no build step, whatever is committed there IS what GitHub
Pages serves -- there is no later stage that would notice a half-finished
update. This module is that missing stage: it asserts the generated artifacts
in docs/data/ actually agree with each other before a deploy publishes them.

It deliberately checks only invariants that hold across *any* correct pipeline
run, never absolute figures that legitimately change month to month.

Run standalone (`python -m pipeline.verify_publish`) or via `make verify`;
the deploy workflow runs it as a gate before publishing.

Note on what is NOT checkable here: tree.json records no dump date of its own,
so it cannot be cross-checked against VERSION's __content_version__ -- that
date exists only in VERSION, stamped by process.py from the dump's filename.
The changelog is the artifact that does carry dates, which is why the staleness
check below is anchored to it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
DATA = DOCS / "data"

# Frontend fetch targets: app.js reads tree.json, about.js reads changelog.json
# and source_eras.json. A missing one is a broken page, not a degraded one.
REQUIRED_DATA_FILES = ("tree.json", "changelog.json", "source_eras.json")


class VerificationError(Exception):
    """A committed-artifact inconsistency that should block publishing."""


def _read_version() -> dict[str, str]:
    """Parses docs/VERSION's `__key__ = "value"` lines."""
    text = (DOCS / "VERSION").read_text(encoding="utf-8")
    return dict(re.findall(r'^(__\w+__)\s*=\s*"([^"]*)"', text, re.MULTILINE))


def check_required_files() -> list[str]:
    notes = []
    for name in REQUIRED_DATA_FILES:
        path = DATA / name
        if not path.exists():
            raise VerificationError(
                f"docs/data/{name} is missing -- the frontend fetches it directly, "
                f"so publishing without it ships a broken page."
            )
        if path.stat().st_size == 0:
            raise VerificationError(f"docs/data/{name} is empty.")
        notes.append(f"{name} present ({path.stat().st_size:,} bytes)")
    return notes


def check_version_fields() -> list[str]:
    version = _read_version()
    for field in ("__code_version__", "__data_version__", "__content_version__"):
        if not version.get(field):
            raise VerificationError(f"docs/VERSION is missing {field}.")
    for field in ("__data_version__", "__content_version__"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version[field]):
            raise VerificationError(
                f"docs/VERSION's {field} is {version[field]!r}, expected YYYY-MM-DD."
            )
    return [f"VERSION: {' '.join(f'{k}={v}' for k, v in version.items())}"]


def check_changelog_covers_current_dump() -> list[str]:
    """The failure this whole module exists for.

    `make process` stamps VERSION's __content_version__ to the new dump and
    writes tree.json, but does NOT write the backfill snapshot the changelog is
    built from. If `make backfill` is then skipped -- or `make regen-changelog`
    is used instead, which derives its month list by globbing existing snapshots
    and so omits a brand-new month *silently, with no error* -- the About page
    ships a history whose newest entry predates the corpus the rest of the site
    is showing. Both are documented footguns in CLAUDE.md's monthly sequence.
    """
    content_version = _read_version()["__content_version__"]
    changelog = json.loads((DATA / "changelog.json").read_text(encoding="utf-8"))
    if not changelog:
        raise VerificationError("docs/data/changelog.json is an empty array.")

    # Entry dates are ISO timestamps ("2026-08-01T00:00:00Z"); compare by date.
    newest = max(entry["date"] for entry in changelog)[:10]
    if newest != content_version:
        raise VerificationError(
            f"changelog.json's newest entry is {newest}, but VERSION's "
            f"__content_version__ is {content_version}.\n"
            f"  The site would publish {content_version} corpus data alongside a "
            f"history that stops at {newest}.\n"
            f"  Fix: run `make backfill` (NOT `make regen-changelog`, which skips "
            f"months lacking a snapshot) so the {content_version} transition is diffed."
        )
    return [f"changelog newest entry {newest} matches __content_version__"]


def check_changelog_continuity() -> list[str]:
    """Each entry should diff against the previous entry, leaving no month gap.

    A break means a month-pair was never diffed, so the About page's trend
    charts silently omit a transition -- the same class of quiet wrongness the
    staleness check guards, one month further back.
    """
    changelog = json.loads((DATA / "changelog.json").read_text(encoding="utf-8"))
    entries = sorted(changelog, key=lambda e: e["date"])
    breaks = [
        f"{prev['date'][:10]} -> {cur['date'][:10]} (entry claims old_date {cur['old_date'][:10]})"
        for prev, cur in zip(entries, entries[1:])
        if cur.get("old_date", "")[:10] != prev["date"][:10]
    ]
    if breaks:
        raise VerificationError(
            "changelog.json has gaps -- these consecutive entries do not chain:\n  "
            + "\n  ".join(breaks)
            + "\n  Fix: rerun `make backfill` to rebuild the changelog from scratch."
        )
    return [f"changelog chains cleanly across {len(entries)} entries"]


def check_tree_shape() -> list[str]:
    """Guards the schema app.js relies on, and that the corpus is non-empty.

    Deliberately not asserting any particular count: the corpus grows every
    month, so a fixed threshold would either be meaningless or need constant
    bumping. Zero, though, means a broken build rather than a small corpus.
    """
    tree = json.loads((DATA / "tree.json").read_text(encoding="utf-8"))
    if "root" not in tree:
        raise VerificationError("tree.json has no 'root' key.")

    root = tree["root"]
    for field in ("id", "type", "title", "children", "stats"):
        if field not in root:
            raise VerificationError(f"tree.json root is missing '{field}'.")

    stats = root["stats"]
    for metric in ("transliterated_bytes", "count", "text_count"):
        if metric not in stats:
            raise VerificationError(f"tree.json root stats is missing '{metric}'.")
        if not isinstance(stats[metric], int) or stats[metric] <= 0:
            raise VerificationError(
                f"tree.json root stats.{metric} is {stats[metric]!r} -- expected a "
                f"positive integer. A zero here means the build produced an empty tree."
            )

    if not root["children"]:
        raise VerificationError("tree.json root has no children -- empty category tree.")

    return [
        f"tree.json root: {stats['text_count']:,} texts, {stats['count']:,} items, "
        f"{len(root['children'])} top-level categories"
    ]


CHECKS = (
    ("required data files", check_required_files),
    ("VERSION fields", check_version_fields),
    ("changelog covers current dump", check_changelog_covers_current_dump),
    ("changelog continuity", check_changelog_continuity),
    ("tree.json shape", check_tree_shape),
)


def main() -> int:
    failures = []
    for label, check in CHECKS:
        try:
            for note in check():
                print(f"  ok   {label}: {note}")
        except VerificationError as exc:
            failures.append((label, exc))
            print(f"  FAIL {label}: {exc}", file=sys.stderr)
        except Exception as exc:  # unreadable/malformed artifact
            failures.append((label, exc))
            print(f"  FAIL {label}: {type(exc).__name__}: {exc}", file=sys.stderr)

    if failures:
        print(
            f"\n{len(failures)} of {len(CHECKS)} publish checks failed -- "
            f"docs/ is not consistent, refusing to publish.",
            file=sys.stderr,
        )
        return 1

    print(f"\nall {len(CHECKS)} publish checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
