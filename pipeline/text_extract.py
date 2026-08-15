"""Write out the corpus text `process` already computed.

`compute_all_content_sizes()` produces the markup-free Devanagari and its IAST
transliteration for every page, keeps three byte counts, and drops the strings
(`content_cache` blanks them explicitly: "build_tree_json never reads them").
So the whole corpus passes through memory on every `make process` and lands
nowhere.

This is the writer, and **it is a flag on `process`, not a stage of its own**
(`--extract-text`). That matters for one reason: the expansion is expensive and
`process` already parallelizes it across a process pool. A standalone extractor
has to redo the parse, the template index, the pool, the transclusion map, and
the augmentation -- and the first attempt did it serially, taking 17.7 minutes
against `process`'s 3-5 for the same computation. Writing files is the only new
work; everything else is already in hand by the time this is called.

## Layout

    <out>/deva/main/<pageid> - <Title>.txt    Main namespace, markup-free
    <out>/deva/page/<pageid> - <Title>.txt    पृष्ठम् scan leaves
    <out>/iast/...                            the same text in IAST
    <out>/index.jsonl                         path -> pageid, title, byte counts

**The `<pageid>` prefix makes filename collisions impossible**, mirroring how
`e-bharatisampat-atlas` files a book under its serial. Title sanitization is
lossy -- `/` is kept as real directory structure, other hostile characters are
stripped, components are truncated -- and two distinct titles can and do
converge (`भविष्यपुराणम् /पर्व ३` vs `भविष्यपुराणम्/पर्व ३`, differing only by a
space). Without the id such a pair silently overwrites, one page vanishing with
every write reporting success. The pageid is unique per page by definition.

**Main and scan pages stay in separate trees.** A `main/` file is a work; a
`page/` file is one leaf of a scan whose text usually ALSO appears, transcluded,
inside a `main/` page (`_augment_main_sizes_with_transclusion` is where
`process` folds those bytes upward). Flattening the two together would present
the same text twice as if it were two texts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.content_size import ContentSizeResult
from pipeline.parse_dump import PageRecord

# Characters a filesystem or shell will fight over. `/` is deliberately absent:
# MediaWiki subpage titles use it, and keeping it makes the output tree mirror
# the wiki's own hierarchy.
UNSAFE_RE = re.compile(r'[\\:*?"<>|\x00-\x1f]')

# Room for `.txt`, the `<out>/deva/page/` prefix, and the subdirectories a
# subpage title expands into.
MAX_COMPONENT = 120


def safe_path(title: str, pageid: int) -> Path:
    """`title`, `pageid` -> a relative path, keeping `/` as directory structure.

    Lossy in the title part, which is why the id prefix carries uniqueness and
    `index.jsonl` records the exact title. Components are truncated
    individually so a deep subpage keeps its leaf.
    """
    parts = []
    for part in title.split("/"):
        clean = UNSAFE_RE.sub("", part).strip()
        parts.append(clean[:MAX_COMPONENT].strip() or "_")
    # `+ ".txt"`, NOT `Path.with_suffix(".txt")`: with_suffix REPLACES whatever
    # follows the last dot, and these titles are full of dots that are not
    # extensions. `ऋग्वेदः सूक्तं १०.१` became `ऋग्वेदः सूक्तं १०.txt`, collapsing
    # all 191 hymns of maṇḍala 10 onto one file.
    parts[-1] = f"{pageid} - {parts[-1]}.txt"
    return Path(*parts)


def write_text_extract(
    out: Path,
    main_records: list[PageRecord],
    main_sizes: dict[str, ContentSizeResult],
    page_records: list[PageRecord],
    page_sizes: dict[str, ContentSizeResult],
) -> dict:
    """Write both scripts for both namespaces; return a summary dict.

    Takes the already-computed `ContentSizeResult`s rather than records alone --
    the whole point is that no expansion or transliteration happens here.

    A page whose stripped text is empty (redirect, stub, pure markup) is
    counted and skipped rather than written as a 0-byte file. A page with no
    `transliterated_text` still gets its Devanagari written; that is the
    `--no-transliterate` case, not an error.
    """
    out.mkdir(parents=True, exist_ok=True)
    summary = {"written": 0, "empty": 0, "collisions": [], "per_ns": {}}
    seen: dict[str, str] = {}

    with (out / "index.jsonl").open("w", encoding="utf-8") as index:
        for label, records, sizes in (("main", main_records, main_sizes),
                                      ("page", page_records, page_sizes)):
            stats = {"pages": 0, "content_bytes": 0, "translit_bytes": 0}
            for record in records:
                size = sizes.get(record.title)
                if size is None or not size.stripped_text.strip():
                    summary["empty"] += 1
                    continue

                rel = Path(label) / safe_path(record.title, record.pageid)
                key = str(rel)
                if key in seen:
                    # Cannot happen with the pageid prefix, but a collision
                    # here is a silently lost page -- every write succeeds --
                    # so it is checked rather than assumed.
                    summary["collisions"].append((key, seen[key], record.title))
                else:
                    seen[key] = record.title

                deva_path = out / "deva" / rel
                deva_path.parent.mkdir(parents=True, exist_ok=True)
                deva_path.write_text(size.stripped_text, encoding="utf-8")

                if size.transliterated_text:
                    iast_path = out / "iast" / rel
                    iast_path.parent.mkdir(parents=True, exist_ok=True)
                    iast_path.write_text(size.transliterated_text,
                                         encoding="utf-8")

                index.write(json.dumps({
                    "pageid": record.pageid,
                    "ns": label,
                    "title": record.title,
                    "path": key,
                    "raw_wikitext_bytes": size.raw_wikitext_bytes,
                    "content_bytes": size.content_bytes,
                    "transliterated_bytes": size.transliterated_bytes,
                }, ensure_ascii=False) + "\n")

                summary["written"] += 1
                stats["pages"] += 1
                stats["content_bytes"] += size.content_bytes
                stats["translit_bytes"] += size.transliterated_bytes
            summary["per_ns"][label] = stats

    return summary
