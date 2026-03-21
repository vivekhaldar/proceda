# ABOUTME: Downloads the Northwind SQLite database from a public GitHub repository.
# ABOUTME: Exposes ensure_db() which skips download if the database file already exists.

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

DB_URL = "https://raw.githubusercontent.com/jpwhite3/northwind-SQLite3/main/dist/northwind.db"

DEFAULT_DB_PATH = Path(__file__).parent / "northwind.db"


def ensure_db(db_path: Path | None = None) -> Path:
    """Download the Northwind database if it doesn't already exist."""
    path = db_path or DEFAULT_DB_PATH
    if path.exists():
        return path

    print("Downloading Northwind database from GitHub...")
    urllib.request.urlretrieve(DB_URL, str(path))

    print(f"Done. Database saved to {path}")
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    ensure_db(target)
