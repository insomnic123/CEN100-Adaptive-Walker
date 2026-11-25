"""SQLite helpers shared with the Next.js site (same file, same schema)."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_DB = ROOT / "data" / "walker.db"


def _default_db() -> Path:
    # On Windows the database lives in %LOCALAPPDATA%\RollatorRig, outside OneDrive: OneDrive syncing a
    # SQLite file while the site and the watcher both use it causes "disk I/O error"s. web/lib/db.ts
    # picks the same place. WALKER_DB overrides both.
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        return Path(local) / "RollatorRig" / "walker.db"
    return REPO_DB


DB_PATH = Path(os.environ.get("WALKER_DB") or _default_db())
SCHEMA = ROOT / "data" / "schema.sql"


def _migrate() -> None:
    """First use of the new location: copy the courses and runs from the old data/walker.db."""
    if DB_PATH.exists() or DB_PATH == REPO_DB or not REPO_DB.exists():
        return
    try:
        src = sqlite3.connect(f"file:{REPO_DB}?mode=ro", uri=True, timeout=5)
        dst = sqlite3.connect(DB_PATH)
        src.backup(dst)
        dst.close()
        src.close()
        print(f"  moved the database out of OneDrive: {DB_PATH}")
    except sqlite3.Error as e:
        print(f"  note: couldn't copy the old database ({e}); starting a fresh one at {DB_PATH}")
        try:
            DB_PATH.unlink()
        except OSError:
            pass


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _migrate()
    con = sqlite3.connect(DB_PATH, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA.read_text())
    return con


def next_queued_run(con: sqlite3.Connection):
    try:
        return con.execute("SELECT * FROM runs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None                                   # busy right now; checked again in 0.5 s


def fail_stale_runs(con: sqlite3.Connection) -> int:
    """Runs left 'running' by a watcher that stopped mid-run: mark them as errors."""
    cur = con.execute("UPDATE runs SET status='error', message='the watcher stopped during this run; "
                      "run it again' WHERE status='running'")
    con.commit()
    return cur.rowcount


def set_run(con: sqlite3.Connection, run_id: int, *, best_effort: bool = False, **fields) -> None:
    """Updates a run row, retrying briefly: the file may be busy (the website, or OneDrive syncing it).
    best_effort=True (progress updates) gives up quietly instead of raising."""
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    for attempt in range(1 if best_effort else 6):
        try:
            con.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
            con.commit()
            return
        except sqlite3.OperationalError:
            try:
                con.rollback()
            except sqlite3.Error:
                pass
            if best_effort:
                return
            time.sleep(0.3 * (attempt + 1))
    con.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
    con.commit()


def abort_requested(con: sqlite3.Connection, run_id: int) -> bool:
    try:
        row = con.execute("SELECT abort_requested FROM runs WHERE id=?", (run_id,)).fetchone()
    except sqlite3.OperationalError:
        return False                                  # busy right now; asked again next time
    return bool(row and row["abort_requested"])
