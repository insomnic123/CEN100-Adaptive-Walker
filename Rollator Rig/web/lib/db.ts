import "server-only";
import { DatabaseSync } from "node:sqlite"; // built into Node 22.13+: no native package to install
import fs from "node:fs";
import path from "node:path";

// The repo root is one level above web/. Python's pipeline/rig/db.py opens the same file.
const ROOT = path.resolve(process.cwd(), "..");
const DB_PATH = process.env.WALKER_DB ?? path.join(ROOT, "data", "walker.db");
const SCHEMA = path.join(ROOT, "data", "schema.sql");

let db: DatabaseSync | null = null;

export function getDb(): DatabaseSync {
  if (!db) {
    fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
    db = new DatabaseSync(DB_PATH);
    db.exec("PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;");
    db.exec(fs.readFileSync(SCHEMA, "utf8"));
  }
  return db;
}
