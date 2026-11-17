"""Stands in for llama-server or `ollama serve` in test_llm.py: answers /health and /api/version, nothing else.
The port comes from --port (llama-server) or OLLAMA_HOST (ollama serve), as with the real ones."""

import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

if os.environ.get("FAKE_SERVER_EXIT"):  # a server that can't start, for example a GPU it doesn't support
    sys.exit(int(os.environ["FAKE_SERVER_EXIT"]))
args = sys.argv[1:]
port = int(args[args.index("--port") + 1]) if "--port" in args else urlparse(os.environ["OLLAMA_HOST"]).port


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path in ("/health", "/api/version") else 404)
        self.end_headers()

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
