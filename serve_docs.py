#!/usr/bin/env python3
"""Local dev server for docs/, gzip-compressing responses and setting
Cache-Control so repeated reloads during iteration (and testing over an
ngrok tunnel on a mobile data plan) don't re-transfer the full uncompressed
tree.json/changelog.json on every load. python -m http.server does neither.
"""
import gzip
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = 8001
CACHE_MAX_AGE = 60  # seconds; short, so edits during iteration aren't stale for long
COMPRESSIBLE_SUFFIXES = {".json", ".js", ".html", ".css", ".svg"}


class CachingGzipHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", f"max-age={CACHE_MAX_AGE}")
        super().end_headers()

    def do_GET(self):
        path = self.translate_path(self.path)
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        if accepts_gzip and Path(path).suffix in COMPRESSIBLE_SUFFIXES and Path(path).is_file():
            self._serve_gzipped(path)
        else:
            super().do_GET()

    def _serve_gzipped(self, path):
        raw = Path(path).read_bytes()
        compressed = gzip.compress(raw)
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(compressed)))
        self.end_headers()
        self.wfile.write(compressed)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = HTTPServer(("", port), CachingGzipHandler)
    print(f"Serving docs/ on http://localhost:{port} (gzip + Cache-Control: max-age={CACHE_MAX_AGE})")
    server.serve_forever()


if __name__ == "__main__":
    main()
