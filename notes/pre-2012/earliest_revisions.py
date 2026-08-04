#!/usr/bin/env python3
"""Find the earliest surviving revision timestamps in the meta-history dump,
to see when real content edits actually begin (the 2004-07-23 cutoff
materialized 0 pages, so the wiki's 'first edit' date is probably a site/log
event or a since-deleted page, not surviving content)."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # notes/pre-2012/ -> repo root
DUMP = str(REPO / "dump" / "_materialize_src"
          / "sawikisource-latest-pages-meta-history.xml")


def localname(tag):
    return tag.rsplit("}", 1)[-1]


def main():
    earliest = []  # (timestamp, title)
    cur_title = None
    n_pages = 0
    for event, elem in ET.iterparse(DUMP, events=("end",)):
        tag = localname(elem.tag)
        if tag == "title" and cur_title is None:
            cur_title = elem.text
        elif tag == "timestamp":
            ts = elem.text
            if ts and ts < "2006-01-01":
                earliest.append((ts, cur_title))
        elif tag == "page":
            n_pages += 1
            cur_title = None
            elem.clear()
            if n_pages % 20000 == 0:
                print(f"...{n_pages} pages, {len(earliest)} pre-2006 revs", file=sys.stderr)

    earliest.sort()
    print(f"\ntotal pre-2006 revisions: {len(earliest)}")
    print("\n--- 40 earliest surviving revisions ---")
    for ts, title in earliest[:40]:
        print(f"{ts}  {title}")


if __name__ == "__main__":
    main()
