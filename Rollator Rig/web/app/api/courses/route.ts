import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { isCourse } from "@/lib/validate";

// GET /api/courses          -> saved courses, newest first
// POST /api/courses {course} -> { id }
export async function GET() {
  const rows = getDb()
    .prepare("SELECT id, name, json, created_at FROM courses ORDER BY id DESC LIMIT 50")
    .all() as { id: number; name: string; json: string; created_at: string }[];
  return NextResponse.json(rows.map((r) => ({ id: r.id, name: r.name, created_at: r.created_at, course: JSON.parse(r.json) })));
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => null);
  if (!isCourse(body?.course)) return NextResponse.json({ error: "invalid course" }, { status: 400 });
  const info = getDb()
    .prepare("INSERT INTO courses (name, json) VALUES (?, ?)")
    .run(body.course.name || "Untitled course", JSON.stringify(body.course));
  return NextResponse.json({ id: Number(info.lastInsertRowid) });
}
