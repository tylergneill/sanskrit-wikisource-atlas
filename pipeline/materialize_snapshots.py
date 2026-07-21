#!/usr/bin/env python3
"""
materialize_snapshots.py — reconstruct monthly full snapshots of a MediaWiki
wiki from a single pages-meta-history XML dump.

Motivation
----------
Wikimedia only retains the most recent dump runs, mirrors carry a ~5-run
tail, and the Internet Archive's volunteer pipeline for sawikisource stalled
(fulls at 2022-05-01, incrementals at 2023-11-23). The snapshot *files* for
the gap are gone, but the *content* is not: the current pages-meta-history
dump contains every surviving revision ever made. For a cutoff date D, the
state of the wiki at D is simply, for each page, the newest revision with
timestamp <= D. This script materializes one pages-articles-style XML file
per requested cutoff, in a single streaming pass over the meta-history file.

Known deviations from the "real" dump of that month (all inherent to the
source data, not to this script):
  1. Pages deleted after the meta-history dump was taken are absent.
     (Pages deleted *before* it are absent too — a real dump of month D
     would have included any page still live at D. Diff against an older
     full dump, e.g. IA sawikisource-20220501, to recover those.)
  2. Titles/namespaces are as of the meta-history dump: a page renamed
     after D appears under its later title. (The public move log can be
     used to back-map titles if ever needed.)
  3. <redirect> elements are re-derived heuristically from wikitext
     (a leading "#MAGICWORD [[...]]" line), since redirect status in the
     dump header reflects only the present.
  4. Revisions whose text was admin-suppressed (deleted="deleted") are
     skipped in favor of the newest *visible* revision <= D.
  5. Official dumps cut at run time, not midnight; revisions from the
     cutoff day itself may differ at the boundary. We cut at 00:00:00 UTC
     of the cutoff date, exclusive of nothing (<= D 00:00:00 means a
     revision stamped exactly midnight is included).

Usage
-----
    python3 materialize_snapshots.py DUMP [options]

    DUMP    pages-meta-history file: .xml, .xml.bz2, .xml.gz, or "-" for
            stdin (e.g. pipe from 7z:  7z x -so dump.7z | ... -)

Options:
    --start YYYY-MM     first monthly cutoff (default 2023-12)
    --end   YYYY-MM     last monthly cutoff  (default 2025-11)
    --day   N           day-of-month for each cutoff (default 1)
    --dates D1,D2,...   explicit ISO dates instead of --start/--end
    --outdir DIR        output directory (default ./snapshots)
    --compress          bz2-compress the outputs
    --keep-empty        emit pages whose chosen revision has empty text
                        (default: emitted; flag kept for symmetry — see code)

Example (fill the sawikisource gap):
    python3 materialize_snapshots.py \
        sawikisource-20260701-pages-meta-history.xml.bz2 \
        --start 2023-12 --end 2025-11 --outdir gap_snapshots --compress

Outputs are named  {dbname}-{YYYYMMDD}-pages-articles.synth.xml[.bz2]
("synth" marks them as reconstructions, so they can't be mistaken for
Wikimedia-produced artifacts, e.g. in checksum manifests).

Memory profile: one <page> subtree at a time; all cutoff files are open
simultaneously and written page-by-page, so the pass is O(pages) time and
O(1) memory regardless of how many cutoffs are requested.
"""

import argparse
import bz2
import gzip
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# Redirect heuristic: MediaWiki redirects always begin "#MAGICWORD [[target]]".
# Matching any "#word(s) [[" tolerates localized magic words (e.g. Sanskrit
# aliases) without hardcoding them. False positives are only possible on a
# page whose very first characters are "#something [[", which ordinary
# wikitext essentially never is.
REDIRECT_RE = re.compile(r"^\s*#\s*\S[^\[\]\n]{0,60}?\[\[(?P<target>[^\]\|\n]+)")


def localname(tag: str) -> str:
    """Strip the XML namespace ('{ns}page' -> 'page')."""
    return tag.rsplit("}", 1)[-1]


def open_dump(path: str):
    """Open the dump as a binary stream, transparently decompressing."""
    if path == "-":
        return sys.stdin.buffer
    if path.endswith(".bz2"):
        return bz2.open(path, "rb")
    if path.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def parse_ts(ts: str) -> datetime:
    """MediaWiki export timestamps: 2024-03-17T09:41:02Z"""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def monthly_cutoffs(start: str, end: str, day: int):
    """Yield datetimes for `day` of each month from start to end inclusive."""
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield datetime(y, m, day, tzinfo=timezone.utc)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def serialize(elem: ET.Element) -> str:
    """Serialize a subtree to a unicode string without XML declaration."""
    return ET.tostring(elem, encoding="unicode")


class SnapshotWriter:
    """One output file per cutoff; header written lazily, footer on close."""

    def __init__(self, cutoff: datetime, outdir: Path, dbname: str, compress: bool):
        self.cutoff = cutoff
        stem = f"{dbname}-{cutoff.strftime('%Y%m%d')}-pages-articles.synth.xml"
        self.path = outdir / (stem + (".bz2" if compress else ""))
        self._fh = (bz2.open(self.path, "wt", encoding="utf-8") if compress
                    else open(self.path, "w", encoding="utf-8"))
        self.pages = 0

    def write_header(self, mediawiki_open: str, siteinfo_xml: str):
        self._fh.write(mediawiki_open + "\n")
        if siteinfo_xml:
            self._fh.write(siteinfo_xml)

    def write_page(self, page_xml: str):
        self._fh.write(page_xml)
        self.pages += 1

    def close(self):
        self._fh.write("</mediawiki>\n")
        self._fh.close()


def build_page_xml(title, ns, page_id, rev_elem, ns_uri) -> str:
    """Assemble a pages-articles-style <page> from the chosen revision."""
    page = ET.Element("page")
    ET.SubElement(page, "title").text = title
    ET.SubElement(page, "ns").text = ns
    ET.SubElement(page, "id").text = page_id

    # Re-derive redirect status from the chosen revision's wikitext (see
    # module docstring, deviation #3).
    text_el = rev_elem.find(f"{{{ns_uri}}}text") if ns_uri else rev_elem.find("text")
    text = text_el.text if text_el is not None and text_el.text else ""
    m = REDIRECT_RE.match(text)
    if m:
        ET.SubElement(page, "redirect", {"title": m.group("target").strip()})

    # Copy the revision subtree verbatim, stripping namespaces for a clean
    # standalone document (the wrapper <mediawiki> carries the schema).
    rev_copy = strip_ns(rev_elem)
    page.append(rev_copy)
    ET.indent(page, space="  ")
    return serialize(page) + "\n"


def strip_ns(elem: ET.Element) -> ET.Element:
    """Deep-copy an element with namespaces removed from all tags."""
    out = ET.Element(localname(elem.tag), dict(elem.attrib))
    out.text, out.tail = elem.text, elem.tail
    for child in elem:
        out.append(strip_ns(child))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("dump", help="pages-meta-history .xml/.xml.bz2/.xml.gz, or -")
    ap.add_argument("--start", default="2023-12")
    ap.add_argument("--end", default="2025-11")
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--dates", help="comma-separated ISO dates, overrides start/end")
    ap.add_argument("--outdir", default="snapshots")
    ap.add_argument("--compress", action="store_true")
    args = ap.parse_args()

    if args.dates:
        cutoffs = sorted(
            datetime.strptime(d.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            for d in args.dates.split(",")
        )
    else:
        cutoffs = list(monthly_cutoffs(args.start, args.end, args.day))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    writers = None          # created once we know the dbname
    siteinfo_xml = ""       # captured verbatim from the source dump
    mediawiki_open = "<mediawiki>"  # replaced with the real root tag + attrs
    ns_uri = ""             # export schema namespace, discovered from root
    dbname = "wiki"

    # Streaming parse. "start" events let us grab the root tag/namespace;
    # "end" events hand us complete <siteinfo> and <page> subtrees.
    stream = open_dump(args.dump)
    context = ET.iterparse(stream, events=("start", "end"))

    pages_seen = 0
    for event, elem in context:
        tag = localname(elem.tag)

        if event == "start" and tag == "mediawiki":
            # Reconstruct the opening tag with its attributes so outputs
            # declare the same export schema version as the source.
            if elem.tag.startswith("{"):
                ns_uri = elem.tag[1:].split("}")[0]
            # Restore the reserved "xml:" prefix, which iterparse expands
            # to Clark notation ({http://www.w3.org/XML/1998/namespace}lang).
            XML_NS = "{http://www.w3.org/XML/1998/namespace}"
            attrs = " ".join(
                f'{("xml:" + k[len(XML_NS):]) if k.startswith(XML_NS) else k}="{v}"'
                for k, v in elem.attrib.items()
            )
            xmlns = f' xmlns="{ns_uri}"' if ns_uri else ""
            mediawiki_open = f"<mediawiki{xmlns}{' ' + attrs if attrs else ''}>"
            continue

        if event != "end":
            continue

        if tag == "siteinfo":
            siteinfo_xml = serialize(strip_ns(elem)) + "\n"
            db = elem.find(f"{{{ns_uri}}}dbname") if ns_uri else elem.find("dbname")
            if db is not None and db.text:
                dbname = db.text
            # Now we can open the outputs and write headers.
            writers = [SnapshotWriter(c, outdir, dbname, args.compress) for c in cutoffs]
            for w in writers:
                w.write_header(mediawiki_open, siteinfo_xml)
            elem.clear()

        elif tag == "page":
            if writers is None:  # dump had no <siteinfo>; open outputs now
                writers = [SnapshotWriter(c, outdir, dbname, args.compress) for c in cutoffs]
                for w in writers:
                    w.write_header(mediawiki_open, "")

            pages_seen += 1
            q = (lambda t: f"{{{ns_uri}}}{t}") if ns_uri else (lambda t: t)
            title = (elem.findtext(q("title")) or "")
            ns = (elem.findtext(q("ns")) or "0")
            page_id = (elem.findtext(q("id")) or "")

            # Collect (timestamp, visible?, element) for every revision.
            revs = []
            for rev in elem.findall(q("revision")):
                ts_text = rev.findtext(q("timestamp"))
                if not ts_text:
                    continue
                text_el = rev.find(q("text"))
                hidden = text_el is not None and text_el.get("deleted") == "deleted"
                revs.append((parse_ts(ts_text), not hidden, rev))
            revs.sort(key=lambda r: r[0])

            # For each cutoff, newest visible revision <= cutoff (deviation #4).
            for w in writers:
                chosen = None
                for ts, visible, rev in revs:
                    if ts > w.cutoff:
                        break
                    if visible:
                        chosen = rev
                if chosen is not None:
                    w.write_page(build_page_xml(title, ns, page_id, chosen, ns_uri))

            elem.clear()  # free the subtree; keeps memory flat

    if writers is None:
        sys.exit("error: no <page> elements found — is this a MediaWiki export?")

    for w in writers:
        w.close()
        print(f"{w.path}  pages={w.pages}")
    print(f"done: {pages_seen} source pages -> {len(writers)} snapshots", file=sys.stderr)


if __name__ == "__main__":
    main()
