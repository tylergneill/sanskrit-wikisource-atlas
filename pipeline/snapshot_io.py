"""Shared gzip-transparent JSON read/write for backfill snapshots and content
caches (pipeline.backfill, pipeline.compare) -- both tree-<date>.json and
content-<date>.json are written gzipped by default (see
notes/richer-backfill-snapshots-plan.md), since 153 months of either at full
uncompressed size adds up, and gzip is a pure win here: cheap to (de)compress
at this size, and these files are always read/written whole, never seeked
into.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path


def write_json_gz(path: Path, data: dict) -> None:
    """Writes `data` as gzip-compressed JSON to `path` (expected to end in
    .json.gz). Uses the same compact separators as the rest of the pipeline's
    snapshot writes -- gzip already handles the repetition, so this isn't
    about pre-shrinking, just consistency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb") as f:
        f.write(payload)


def read_json_gz(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def read_json_maybe_gz(path: Path) -> dict:
    """Reads a JSON snapshot from `path`, transparently handling both a
    gzipped file (detected by a .gz suffix OR the gzip magic bytes, since a
    caller may pass a bare tree-<date>.json path for an already-gzipped file
    written before this suffix convention existed) and a plain-text one --
    lets pipeline.compare and any ad-hoc inspection keep working against
    older, ungzipped snapshots without needing to know which kind a given
    path is."""
    if path.suffix == ".gz":
        return read_json_gz(path)
    with open(path, "rb") as f:
        magic = f.read(2)
    if magic == b"\x1f\x8b":
        return read_json_gz(path)
    return json.loads(path.read_text(encoding="utf-8"))
