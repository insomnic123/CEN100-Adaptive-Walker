import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";

// POST /api/runs/:id/abort -> asks rig_driver.py to stop the rig (it sends X to the Mega)
export async function POST(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const db = getDb();
  db.prepare("UPDATE runs SET abort_requested = 1 WHERE id = ?").run(Number(id));
  // a run nobody has picked up yet can be cancelled straight away
  db.prepare("UPDATE runs SET status = 'aborted', finished_at = datetime('now') WHERE id = ? AND status = 'queued'").run(Number(id));
  return NextResponse.json({ ok: true });
}
