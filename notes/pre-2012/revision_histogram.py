#!/usr/bin/env python3
"""Sanity-check the 2009->2010 flatline: count Main-namespace revisions per
year directly from the meta-history dump. If 2009-07..2010-07 genuinely had
almost no content edits, the flat snapshot numbers are real, not an artifact
of materialization."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter

REPO = Path(__file__).resolve().parents[2]  # notes/pre-2012/ -> repo root
DUMP = str(REPO / "dump" / "_materialize_src"
          / "sawikisource-latest-pages-meta-history.xml")


def localname(tag):
    return tag.rsplit("}", 1)[-1]


def main():
    by_year = Counter()      # all namespaces
    main_by_year = Counter()  # ns 0 only
    new_pages_by_year = Counter()  # first-ever revision of a ns-0 page
    cur_title = None
    cur_ns = None
    first_ts = None
    n_pages = 0

    for event, elem in ET.iterparse(DUMP, events=("end",)):
        tag = localname(elem.tag)
        if tag == "title" and cur_title is None:
            cur_title = elem.text
        elif tag == "ns" and cur_ns is None:
            cur_ns = elem.text
        elif tag == "timestamp":
            ts = elem.text
            if ts and ts < "2012-08-01":
                y = ts[:7]
                by_year[y] += 1
                if cur_ns == "0":
                    main_by_year[y] += 1
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
        elif tag == "page":
            if cur_ns == "0" and first_ts is not None and first_ts < "2012-08-01":
                new_pages_by_year[first_ts[:7]] += 1
            n_pages += 1
            cur_title = None
            cur_ns = None
            first_ts = None
            elem.clear()
            if n_pages % 40000 == 0:
                print(f"...{n_pages} pages", file=sys.stderr)

    print("\nmonth    all_revs  main_revs  new_main_pages")
    for y in sorted(set(by_year) | set(main_by_year) | set(new_pages_by_year)):
        print(f"{y}  {by_year[y]:8}  {main_by_year[y]:9}  {new_pages_by_year[y]:14}")


if __name__ == "__main__":
    main()
