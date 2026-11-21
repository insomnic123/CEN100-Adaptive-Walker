-- Shared by the Next.js site (web/lib/db.ts) and Python (pipeline/rig/db.py).
CREATE TABLE IF NOT EXISTS courses (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  json        TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- status: queued -> running -> done | error | aborted
-- abort_requested is set by the site; rig_driver.py sees it and stops the rig.
CREATE TABLE IF NOT EXISTS runs (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id        INTEGER REFERENCES courses(id),
  course_json      TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'queued',
  abort_requested  INTEGER NOT NULL DEFAULT 0,
  progress_s       REAL NOT NULL DEFAULT 0,
  duration_s       REAL,
  message          TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  started_at       TEXT,
  finished_at      TEXT
);
