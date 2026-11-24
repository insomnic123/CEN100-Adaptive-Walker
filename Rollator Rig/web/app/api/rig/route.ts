import { NextResponse, connection } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { DEFAULT_DECK, type DeckSettings } from "@/lib/course";

// GET /api/rig -> deck settings from data/deck.json (written by `rig_driver.py trim`) and the
// wheel speed range from data/calibration.json (written by `rig_driver.py calibrate`), so the
// course checks match what the compiler will accept.
const DATA = path.join(path.resolve(process.cwd(), ".."), "data");
const DECK_PATH = path.join(DATA, "deck.json");
const CAL_PATH = path.join(DATA, "calibration.json");

type Side = { table: [number, number][] };

export async function GET() {
  await connection(); // read the file on every request, never at build time
  let deck: DeckSettings = { ...DEFAULT_DECK };
  try {
    const raw = JSON.parse(fs.readFileSync(DECK_PATH, "utf8"));
    deck = {
      yaw_trim: Number.isFinite(raw.yaw_trim) ? raw.yaw_trim : 0,
      yaw_sign: raw.yaw_sign > 0 ? 1 : -1,
    };
  } catch {
    /* no file yet: untrimmed */
  }
  try {
    const cal = JSON.parse(fs.readFileSync(CAL_PATH, "utf8")) as { measured?: boolean; left: Side; right: Side };
    if (cal.measured) {
      const sides = [cal.left, cal.right];
      deck.wheel_max_rps = Math.min(...sides.map((s) => s.table.at(-1)![1]));
      deck.wheel_min_rps = Math.max(...sides.map((s) => Math.min(...s.table.map((r) => r[1]).filter((r) => r > 0))));
    }
  } catch {
    /* not calibrated yet: the typical FS90R limits apply */
  }
  return NextResponse.json(deck);
}
