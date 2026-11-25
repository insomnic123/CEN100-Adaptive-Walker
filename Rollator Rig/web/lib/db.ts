import "server-only";
import { DatabaseSync } from "node:sqlite"; // built into Node 22.13+: no native package to install
import fs from "node:fs";
import path from "node:path";

// The repo root is one level above web/. Python's pipeline/rig/db.py opens the same file.
// On Windows that file lives in %LOCALAPPDATA%\RollatorRig, outside OneDrive (OneDrive syncing a
// SQLite file while the site and the watcher both use it causes "disk I/O error"s). WALKER_DB overrides.
const ROOT = path.resolve(process.cwd(), "..");
const REPO_DB = path.join(ROOT, "data", "walker.db");
const DB_PATH =
  process.env.WALKER_DB ||
  (process.platform === "win32" && process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, "RollatorRig", "walker.db")
    : REPO_DB);
const SCHEMA = path.join(ROOT, "data", "schema.sql");

// First use of the new location: bring the old database (courses, runs) along.
function migrate() {
  if (DB_PATH === REPO_DB || fs.existsSync(DB_PATH) || !fs.existsSync(REPO_DB)) return;
  try {
    for (const ext of ["", "-wal", "-shm"])
      if (fs.existsSync(REPO_DB + ext)) fs.copyFileSync(REPO_DB + ext, DB_PATH + ext);
  } catch {
    /* start a fresh database instead */
  }
}

let db: DatabaseSync | null = null;

export function getDb(): DatabaseSync {
  if (!db) {
    fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
    migrate();
    db = new DatabaseSync(DB_PATH);
    db.exec("PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;");
    db.exec(fs.readFileSync(SCHEMA, "utf8"));
  }
  return db;
}
