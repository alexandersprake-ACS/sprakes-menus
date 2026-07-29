"""Serve ./site locally for preview:  python3 scripts/serve.py [port]"""
import http.server
import os
import sys

SITE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "site"))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8123


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=SITE, **kw)


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
