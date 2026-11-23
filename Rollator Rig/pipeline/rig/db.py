"""SQLite helpers shared with the Next.js site (same file, same schema)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("WALKER_DB", ROOT / "data" / "walker.db"))
SCHEMA = ROOT / "data" / "schema.sql"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA.read_text())
    return con


def next_queued_run(con: sqlite3.Connection):
    return con.execute("SELECT * FROM runs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()


def set_run(con: sqlite3.Connection, run_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
    con.commit()


def abort_requested(con: sqlite3.Connection, run_id: int) -> bool:
    row = con.execute("SELECT abort_requested FROM runs WHERE id=?", (run_id,)).fetchone()
    return bool(row and row["abort_requested"])
