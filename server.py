#!/usr/bin/env python3
"""HTTP server for the burr puzzle animation UI.

Serves static files and provides a POST /api/solve endpoint that runs the
BurrTools solver and returns full solution data (keyframes, transforms, etc.)
suitable for Three.js animation.

Usage:
    python server.py [--port 8765]
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from normalize import is_connected, is_puzzle_id, parse_piece_id, parse_puzzle_id
from solve import solve_full

DB_PATH = Path(__file__).resolve().parent / "solve_cache.db"


def _init_db():
    """Create the cache table if it doesn't exist and return a connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS solve_cache ("
        "  piece_ids TEXT PRIMARY KEY,"
        "  result TEXT NOT NULL"
        ")"
    )
    conn.commit()
    return conn


def _cache_key(piece_ids):
    """Canonical cache key from a list of piece IDs (order-preserving)."""
    return ",".join(str(pid) for pid in piece_ids)


class PuzzleHandler(SimpleHTTPRequestHandler):
    """Serve static files and handle /api/solve requests."""

    db = None  # set in main()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            query = f"?{parsed.query}" if parsed.query else ""
            self.send_response(302)
            self.send_header("Location", f"/anim.html{query}")
            self.end_headers()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/solve":
            self._handle_solve()
        else:
            self.send_error(404, "Not Found")

    def _handle_solve(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
        except (ValueError, json.JSONDecodeError) as e:
            self._json_error(400, f"Invalid JSON: {e}")
            return

        pieces_str = data.get("pieces", "").strip()
        if not pieces_str:
            self._json_error(400, "Missing 'pieces' field")
            return

        # Parse piece IDs (same formats as CLI: decimal, 0x hex, 0b binary,
        # or a single 18-hex-digit puzzle ID)
        try:
            tokens = pieces_str.split()
            if len(tokens) == 1 and is_puzzle_id(tokens[0]):
                piece_ids = parse_puzzle_id(tokens[0])
            else:
                piece_ids = [parse_piece_id(t) for t in tokens]
        except ValueError as e:
            self._json_error(400, str(e))
            return

        if len(piece_ids) != 6:
            self._json_error(400, f"Expected 6 pieces, got {len(piece_ids)}")
            return

        for pid in piece_ids:
            if not (1 <= pid <= 4096):
                self._json_error(400, f"Piece ID {pid} out of range (1-4096)")
                return
            if not is_connected(pid):
                self._json_error(400, f"Piece {pid} is disconnected")
                return

        # Check cache
        key = _cache_key(piece_ids)
        row = self.db.execute(
            "SELECT result FROM solve_cache WHERE piece_ids = ?", (key,)
        ).fetchone()
        if row:
            self._json_response(200, json.loads(row[0]))
            return

        try:
            result = solve_full(piece_ids)
        except FileNotFoundError as e:
            self._json_error(500, str(e))
            return
        except subprocess.TimeoutExpired:
            self._json_error(500, "Solver timed out after 5 minutes")
            return
        except Exception as e:
            self._json_error(500, f"Solver error: {e}")
            return

        # Store in cache
        self.db.execute(
            "INSERT OR REPLACE INTO solve_cache (piece_ids, result) VALUES (?, ?)",
            (key, json.dumps(result)),
        )
        self.db.commit()

        self._json_response(200, result)

    def _json_response(self, code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, code, message):
        self._json_response(code, {"error": message})

    def log_message(self, format, *args):
        # Quieter logging: skip GET for static assets
        if args and isinstance(args[0], str) and args[0].startswith("GET"):
            return
        super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser(description="Burr puzzle animation server")
    parser.add_argument(
        "--port", "-p", type=int, default=8765, help="Port to listen on (default 8765)"
    )
    args = parser.parse_args()

    PuzzleHandler.db = _init_db()
    server = HTTPServer(("", args.port), PuzzleHandler)
    print(f"Serving on http://localhost:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.server_close()


if __name__ == "__main__":
    main()
