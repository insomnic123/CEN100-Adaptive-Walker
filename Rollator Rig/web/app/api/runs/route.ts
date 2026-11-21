import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { isCourse } from "@/lib/validate";

// POST /api/runs {course, courseId?} -> { id }
// Queues a run; pipeline/rig_driver.py (watch mode) picks it up and drives the Mega.
export async function POST(req: Request) {
  const body = await req.json().catch(() => null);
  if (!isCourse(body?.course) || body.course.path.length < 2)
    return NextResponse.json({ error: "invalid course" }, { status: 400 });
  const db = getDb();
  const busy = db.prepare("SELECT id FROM runs WHERE status IN ('queued','running') LIMIT 1").get();
  if (busy) return NextResponse.json({ error: "the rig is already busy with another run" }, { status: 409 });
  const info = db
    .prepare("INSERT INTO runs (course_id, course_json) VALUES (?, ?)")
    .run(typeof body.courseId === "number" ? body.courseId : null, JSON.stringify(body.course));
  return NextResponse.json({ id: Number(info.lastInsertRowid) });
}
