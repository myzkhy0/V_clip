"""
check_postgres.py -- Lightweight PostgreSQL reachability check.
"""

from __future__ import annotations

import socket
import sys
from urllib.parse import urlparse

from config import DATABASE_URL


def main() -> int:
    parsed = urlparse(DATABASE_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432

    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"PostgreSQL is reachable at {host}:{port}")
            return 0
    except OSError:
        print(f"ERROR: Could not connect to PostgreSQL at {host}:{port}")
        print("ERROR: Confirm PostgreSQL is running and DATABASE_URL is correct.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
