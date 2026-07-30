#!/usr/bin/env python3
"""What did the two big mass-revision bursts (2006-01, 2011-03) actually do?
Both show ~2-4k Main-namespace revisions but almost zero new pages, so they
were edits to existing content. Sample which pages they touched, and by which
users, to characterize them."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter

REPO = Path(__file__).resolve().parents[2]  # notes/pre-2012/ -> repo root
DUMP = str(REPO / "dump" / "_materialize_src"
          / "sawikisource-latest-pages-meta-history.xml")
WINDOWS = {"2006-01": ("2006-01-01", "2006-02-01"), "2011-03": ("2011-03-01", "2011-04-01")}


def localname(tag):
    return tag.rsplit("}", 1)[-1]


def main():
    users = {k: Counter() for k in WINDOWS}
    titles = {k: Counter() for k in WINDOWS}
    comments = {k: Counter() for k in WINDOWS}
    cur_title = cur_ns = None
    n = 0

    for event, elem in ET.iterparse(DUMP, events=("end",)):
        tag = localname(elem.tag)
        if tag == "title" and cur_title is None:
            cur_title = elem.text
        elif tag == "ns" and cur_ns is None:
            cur_ns = elem.text
        elif tag == "revision":
            ts_el = elem.find("{*}timestamp")
            ts = ts_el.text if ts_el is not None else None
            if ts:
                for key, (lo, hi) in WINDOWS.items():
                    if lo <= ts < hi:
                        u = elem.find("{*}contributor/{*}username")
                        ip = elem.find("{*}contributor/{*}ip")
                        who = u.text if u is not None else (ip.text if ip is not None else "?")
                        users[key][who] += 1
                        titles[key][cur_title] += 1
                        c = elem.find("{*}comment")
                        comments[key][(c.text or "")[:70] if c is not None else ""] += 1
            elem.clear()
        elif tag == "page":
            n += 1
            cur_title = cur_ns = None
            elem.clear()
            if n % 40000 == 0:
                print(f"...{n} pages", file=sys.stderr)

    for key in WINDOWS:
        print(f"\n===== {key} burst =====")
        print(f"total revisions in window: {sum(users[key].values())}")
        print("top editors:")
        for who, c in users[key].most_common(6):
            print(f"  {c:6}  {who}")
        print("top edit comments:")
        for cm, c in comments[key].most_common(6):
            print(f"  {c:6}  {cm!r}")
        print(f"distinct pages touched: {len(titles[key])}")
        print("sample pages:")
        for t, c in titles[key].most_common(8):
            print(f"  {c:4}  {t}")


if __name__ == "__main__":
    main()
