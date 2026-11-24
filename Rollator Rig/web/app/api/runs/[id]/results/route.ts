import { NextResponse, connection } from "next/server";
import fs from "node:fs";
import path from "node:path";

// GET /api/runs/:id/results -> data/runs/run-<id>/results.json, written by rig_driver.py once a
// run's sensor log has been processed and scored. 404 until then (or for runs without the ESP32).
const RUNS = path.join(path.resolve(process.cwd(), ".."), "data", "runs");

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await ctx.params;
  if (!/^\d+$/.test(id)) return NextResponse.json({ error: "bad id" }, { status: 400 });
  try {
    const raw = fs.readFileSync(path.join(RUNS, `run-${id}`, "results.json"), "utf8");
    return new NextResponse(raw, { headers: { "content-type": "application/json" } });
  } catch {
    return NextResponse.json({ error: "no results" }, { status: 404 });
  }
}
