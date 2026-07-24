"""
Fetch stage (legacy): locate, download, and verify historical sawikisource
dumps in the classic MediaWiki export format (pages-meta-current.xml.bz2),
for backfilling the changelog further back than the 3-month rolling window
`pipeline/fetch.py` / mediawiki_content_current provides (see
docs/about.html's changelog note and pipeline/backfill.py).

Two sources, both in the same classic format (same schema
`pipeline/parse_dump.py` already reads, confirmed against a live 2022-01-20
spike run with zero parser changes needed) but covering different date
ranges:

1. **Live rolling window**: https://dumps.wikimedia.org/sawikisource/<date>/
   -- Wikimedia's own still-current copy of the legacy dump format. Also
   just a rolling window (confirmed directly, not assumed), but a *different*
   window than mediawiki_content_current's: as of this writing it holds
   2025-11-20 through 2026-07-01, i.e. it actually reaches right up to (and
   overlaps with) the current-format window's start -- this is what fills
   the gap between the Internet Archive's last snapshot (2022-05) and
   mediawiki_content_current's first (2026-05). Verified via per-directory
   <date>-md5sums.txt (same idea as pipeline/fetch.py's SHA256SUMS, different
   filename/algorithm).

2. **Internet Archive**: item `sawikisource-<YYYYMMDD>`, one item per
   historical dump run, going back to 2011. This is the only source with
   real historical depth -- the live rolling window above only ever holds
   the last ~8 months, same as any rolling window. Verified via each item's
   own /metadata/<id> JSON endpoint (per-file md5/sha1), not a directory-wide
   sums file.

Both are queried live (never hardcoded) since both windows shift over time.
For a given month, the live rolling window is preferred when it has an entry
(fresher, and it's the only source that actually reaches into 2025-2026);
Internet Archive is the fallback/primary source for everything older.

This is entirely separate from pipeline/fetch.py: different source(s),
different naming/verification per source, and used only by
pipeline/backfill.py for historical months -- never by the live monthly
`make process` path.
"""

from __future__ import annotations

import argparse
import bz2
import hashlib
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

IA_METADATA_URL = "https://archive.org/metadata"
IA_DOWNLOAD_URL = "https://archive.org/download"
IDENTIFIER_PREFIX = "sawikisource-"

LIVE_BASE_URL = "https://dumps.wikimedia.org/sawikisource"

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "dump" / "_legacy"

USER_AGENT = (
    "sanskrit-wikisource-mirror/2.0 "
    "(https://github.com/tylergneill/sanskrit-wikisource-mirror; polite; research use)"
)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

# archive.org's metadata/search endpoints occasionally hang or reset under
# load (confirmed live: a bare ReadTimeout on find_archive_snapshot killed a
# 123-step pipeline.backfill run at its second-to-last step, discarding
# nothing already logged but forcing a full rerun of the whole sequence just
# to retry one flaky request) -- retry transient network errors a few times
# with backoff before giving up, rather than letting one hiccup anywhere in
# a long historical walk take down the entire run.
def _get_with_retry(url: str, *, retries: int = 3, backoff: float = 2.0, **kwargs) -> requests.Response:
    import time
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return session.get(url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exc = e
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                print(f"  (transient error on {url}: {e} -- retrying in {wait:.0f}s)", file=sys.stderr)
                time.sleep(wait)
    raise last_exc


@dataclass
class LegacyDump:
    date: str  # YYYY-MM-DD, exact day of the underlying dump run
    source: str  # "live" or "archive"
    download_url: str
    filename: str
    digest_algo: str  # "md5"
    digest: str


def _identifier_to_date(identifier: str) -> str:
    raw = identifier[len(IDENTIFIER_PREFIX):]  # "20220120"
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


# ---------------------------------------------------------------------------
# Source 1: live rolling window (dumps.wikimedia.org/sawikisource/<date>/)
# ---------------------------------------------------------------------------

def list_live_snapshots() -> list[str]:
    """Query the live legacy rolling-window directory listing, returning
    dump dates (YYYYMMDD, as they appear in the directory names) sorted
    chronologically. One HTTP request."""
    resp = _get_with_retry(f"{LIVE_BASE_URL}/", timeout=30)
    resp.raise_for_status()
    dates = re.findall(r'href="(\d{8})/"', resp.text)
    return sorted(set(dates))


def find_live_snapshot(date8: str) -> LegacyDump | None:
    """Look up one live rolling-window date (YYYYMMDD) and return its
    pages-meta-current.xml.bz2 descriptor, or None if that date/file
    doesn't exist there."""
    sums_url = f"{LIVE_BASE_URL}/{date8}/sawikisource-{date8}-md5sums.txt"
    resp = _get_with_retry(sums_url, timeout=30)
    if not resp.ok:
        return None
    filename = f"sawikisource-{date8}-pages-meta-current.xml.bz2"
    digest = None
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        d, _, fname = line.partition("  ")
        if fname.strip() == filename:
            digest = d.strip()
            break
    if digest is None:
        return None
    return LegacyDump(
        date=_identifier_to_date(f"{IDENTIFIER_PREFIX}{date8}"),
        source="live",
        download_url=f"{LIVE_BASE_URL}/{date8}/{filename}",
        filename=filename,
        digest_algo="md5",
        digest=digest,
    )


# ---------------------------------------------------------------------------
# Source 2: Internet Archive (archive.org/metadata/sawikisource-<date>)
# ---------------------------------------------------------------------------

def list_archive_snapshots() -> list[str]:
    """Query the Internet Archive search API for every sawikisource-<date>
    item, returning identifiers sorted chronologically. One HTTP request."""
    resp = _get_with_retry(
        "https://archive.org/advancedsearch.php",
        params={
            "q": f"identifier:{IDENTIFIER_PREFIX}2*",
            "fl[]": "identifier",
            "rows": 500,
            "output": "json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    docs = resp.json()["response"]["docs"]
    return sorted(d["identifier"] for d in docs)


def find_archive_snapshot(identifier: str) -> LegacyDump | None:
    """Look up one Internet Archive item's metadata and return its
    pages-meta-current.xml.bz2 descriptor, or None if that item/file
    doesn't exist."""
    resp = _get_with_retry(f"{IA_METADATA_URL}/{identifier}", timeout=30)
    if not resp.ok:
        return None
    data = resp.json()
    files = data.get("files", [])
    filename = f"{identifier}-pages-meta-current.xml.bz2"
    for f in files:
        if f["name"] == filename:
            return LegacyDump(
                date=_identifier_to_date(identifier),
                source="archive",
                download_url=f"{IA_DOWNLOAD_URL}/{identifier}/{filename}",
                filename=filename,
                digest_algo="md5",
                digest=f["md5"],
            )
    return None


# ---------------------------------------------------------------------------
# Unified month index
# ---------------------------------------------------------------------------

def one_per_month(dates: list[str]) -> dict[str, str]:
    """Given a list of YYYY-MM-DD dates, keep only the earliest date for
    each YYYY-MM, returned as {"YYYY-MM": "YYYY-MM-DD"}."""
    by_month: dict[str, str] = {}
    for date in sorted(dates):
        ym = date[:7]
        by_month.setdefault(ym, date)
    return by_month


def list_available_months() -> dict[str, LegacyDump]:
    """Query both sources and return one LegacyDump per available month
    ({"YYYY-MM": LegacyDump}), live-rolling-window preferred over Internet
    Archive when both have an entry for the same month (see module
    docstring). This is the single entry point pipeline.backfill should use
    -- it never needs to know which underlying source served a given month."""
    live_dates = [_identifier_to_date(f"{IDENTIFIER_PREFIX}{d}") for d in list_live_snapshots()]
    archive_identifiers = list_archive_snapshots()
    archive_dates = [_identifier_to_date(i) for i in archive_identifiers]

    by_month: dict[str, LegacyDump] = {}
    # Archive first (fallback), then live overwrites where it also has the month.
    for date in sorted(archive_dates):
        ym = date[:7]
        if ym not in by_month:
            dump = find_archive_snapshot(f"{IDENTIFIER_PREFIX}{date.replace('-', '')}")
            if dump is not None:
                by_month[ym] = dump
    for date in sorted(live_dates):
        ym = date[:7]
        dump = find_live_snapshot(date.replace("-", ""))
        if dump is not None:
            by_month[ym] = dump  # overwrite: live wins over archive for the same month

    return by_month


def _md5sum(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def download_and_verify(dump: LegacyDump, out_dir: Path, force: bool = False) -> Path:
    """Download dump's bz2 into out_dir, verifying against its published digest.
    Skips re-downloading if already present and verified, unless force is set."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / dump.filename

    if dest.exists() and not force:
        if _md5sum(dest) == dump.digest:
            print(f"already present, verified: {dump.filename}", file=sys.stderr)
            return dest
        print(f"present but hash mismatch, re-downloading: {dump.filename}", file=sys.stderr)

    print(f"downloading ({dump.source}): {dump.download_url}", file=sys.stderr)
    with session.get(dump.download_url, stream=True, timeout=180) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                written += len(chunk)
                if total:
                    pct = 100 * written / total
                    print(f"\r  {dump.filename}: {written / 1e6:.1f}/{total / 1e6:.1f} MB ({pct:.0f}%)",
                          end="", file=sys.stderr)
        print(file=sys.stderr)

    actual_digest = _md5sum(dest)
    if actual_digest != dump.digest:
        raise ValueError(f"{dump.digest_algo} mismatch for {dump.filename}: "
                          f"expected {dump.digest}, got {actual_digest}")
    print(f"verified: {dump.filename}", file=sys.stderr)
    return dest


def _decompress(bz2_path: Path, force: bool = False) -> Path:
    xml_path = bz2_path.with_suffix("")
    if xml_path.exists() and not force:
        if xml_path.stat().st_mtime >= bz2_path.stat().st_mtime:
            print(f"already decompressed: {xml_path.name}", file=sys.stderr)
            return xml_path
    print(f"decompressing: {bz2_path.name}", file=sys.stderr)
    tmp_path = xml_path.with_suffix(".xml.tmp")
    with bz2.open(bz2_path, "rb") as src, open(tmp_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    tmp_path.rename(xml_path)
    print(f"decompressed: {xml_path.name}", file=sys.stderr)
    return xml_path


def fetch_snapshot(dump: LegacyDump, out_dir: Path = DEFAULT_OUT_DIR, force: bool = False) -> Path:
    """Download + verify dump's pages-meta-current.xml.bz2 into
    out_dir/<date>/, and decompress it. Returns the path to the decompressed
    .xml."""
    item_dir = out_dir / dump.date
    bz2_path = download_and_verify(dump, item_dir, force=force)
    return _decompress(bz2_path, force=force)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true",
                         help="list available months (live rolling window + Internet Archive, "
                              "merged/deduped) and exit")
    parser.add_argument("--month", type=str, default=None,
                         help="fetch one specific month, e.g. 2022-01")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                         help=f"directory to download into (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--force", action="store_true",
                         help="re-download and re-verify even if already present and verified locally")
    args = parser.parse_args()

    if args.list:
        by_month = list_available_months()
        for ym in sorted(by_month):
            dump = by_month[ym]
            print(f"{ym}\t{dump.date}\t{dump.source}")
        print(f"\n{len(by_month)} months available", file=sys.stderr)
        return

    if args.month is None:
        parser.error("--month is required unless --list is given")

    by_month = list_available_months()
    dump = by_month.get(args.month)
    if dump is None:
        raise RuntimeError(f"no legacy snapshot found for month {args.month}")
    xml_path = fetch_snapshot(dump, out_dir=args.out_dir, force=args.force)
    print(xml_path)


if __name__ == "__main__":
    main()
