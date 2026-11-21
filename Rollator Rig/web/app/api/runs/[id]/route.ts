import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";

// GET /api/runs/:id -> status and progress, polled by the sim page while a run is live
export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const row = getDb()
    .prepare(
      "SELECT id, status, progress_s, duration_s, message, created_at, started_at, finished_at FROM runs WHERE id = ?",
    )
    .get(Number(id));
  if (!row) return NextResponse.json({ error: "not found" }, { status: 404 });
  return NextResponse.json(row);
}
