"""
Live pinned-status-line progress reporting for pipeline stages, in the same
spirit as scrape.py's CategoryProgress/ActivityStatus -- a long silent phase
is ambiguous between "working" and "hung", and the old scraper's answer to
that (a single carriage-returned status line, repainted often, truncated to
terminal width so it can't wrap and leave garbage behind on clear) applies
just as much to a CPU-bound parallel pass over the dump as it did to a
network-bound crawl, even though the specific "rate-limit backoff" case that
motivated ActivityStatus doesn't apply here.

Difference from scrape.py's model: work here runs on a process pool, so the
main process finds out about a page only when its result arrives, not when a
worker starts on it -- there is no true "fetching: <title> right now" signal
to show. The status line shows the last-*completed* title plus throughput/
ETA instead, which is what's actually knowable from the main process.
"""

from __future__ import annotations

import shutil
import sys
import time


class LiveCounter:
    """Pinned single-line progress: count[/total] with elapsed time, rate,
    and (if total is known) percent complete and ETA. Call update() as
    results arrive; call close() once at the end to leave a final line and
    move to a fresh line for whatever prints next."""

    def __init__(self, label: str, total: int | None = None, min_interval: float = 0.1):
        self.label = label
        self.total = total
        self.count = 0
        self.min_interval = min_interval
        self._start = time.time()
        self._last_paint = 0.0
        self._status_len = 0
        self._last_detail = ""

    def _clear(self) -> None:
        if self._status_len:
            sys.stdout.write("\r" + " " * self._status_len + "\r")

    def _render(self, width: int) -> str:
        elapsed = time.time() - self._start
        rate = self.count / elapsed if elapsed > 0 else 0.0
        parts = [self.label]
        if self.total:
            pct = 100 * self.count / self.total
            parts.append(f"{self.count}/{self.total} ({pct:.0f}%)")
            remaining = self.total - self.count
            eta = remaining / rate if rate > 0 else None
            eta_str = f", ETA {eta:.0f}s" if eta is not None else ""
            parts.append(f"{rate:.1f}/s, {elapsed:.0f}s elapsed{eta_str}")
        else:
            parts.append(f"{self.count} done")
            parts.append(f"{rate:.1f}/s, {elapsed:.0f}s elapsed")
        # The core stats above are the load-bearing part of the line and must
        # always survive truncation whole. "last: <title>" is nice-to-have --
        # appended only if it fits in full, dropped entirely otherwise, so a
        # long (e.g. Devanagari, multi-byte) title never gets sliced mid-word
        # into a dangling "-- " with no text after it.
        core = " -- ".join(parts)
        if self._last_detail:
            with_detail = core + f" -- last: {self._last_detail}"
            if len(with_detail) <= width:
                return with_detail
        return core

    def update(self, detail: str | None = None, n: int = 1, force: bool = False) -> None:
        self.count += n
        if detail is not None:
            self._last_detail = detail
        now = time.time()
        if not force and (now - self._last_paint) < self.min_interval:
            return
        self._last_paint = now
        self._clear()
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
        line = self._render(max(width - 1, 0))
        self._status_len = len(line)
        sys.stdout.write(line)
        sys.stdout.flush()

    def close(self) -> None:
        self.update(n=0, force=True)
        sys.stdout.write("\n")
        sys.stdout.flush()
