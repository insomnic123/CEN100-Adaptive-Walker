// Course geometry. Mirrors pipeline/rig/course.py: keep the two in step.
import type { Course, CourseObject, Pt } from "./types";

// Rig limits (servo rig: continuous-servo wheels, standard-servo deck). Mirror pipeline/rig/compiler.py.
export const WHEEL_MAX_RPS = 1.2; // typical FS90R top speed with margin; calibration may allow more
export const YAW_LIMIT_DEG = 90; // yaw servo travel either side of the starting direction
export const SERVO_LIMIT_DEG = 25;
export const SETUP_S = 6; // standstill + sync tap before the wheels move
export const CURB_PAUSE_S = 1;

// Deck settings from data/deck.json (see pipeline/rig/deck.py), served by /api/rig.
export type DeckSettings = {
  yaw_trim: number;
  yaw_sign: number;
  wheel_max_rps?: number; // from data/calibration.json, once calibrated
  wheel_min_rps?: number; // slowest speed the wheel servos hold smoothly
};
export const DEFAULT_DECK: DeckSettings = { yaw_trim: 0, yaw_sign: -1 };

/** [most right, most left] heading change (degrees, left/CCW positive) the trimmed yaw servo can show. */
export function yawRange(deck: DeckSettings): [number, number] {
  const lo = -YAW_LIMIT_DEG - deck.yaw_trim;
  const hi = YAW_LIMIT_DEG - deck.yaw_trim;
  return deck.yaw_sign > 0 ? [lo, hi] : [-hi, -lo];
}

const dist = (a: Pt, b: Pt) => Math.hypot(b[0] - a[0], b[1] - a[1]);

export function cumulative(path: Pt[]): number[] {
  const cum = [0];
  for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + dist(path[i - 1], path[i]));
  return cum;
}

export function pathLength(path: Pt[]): number {
  return path.length < 2 ? 0 : cumulative(path).at(-1)!;
}

export function pointAt(path: Pt[], s: number): Pt {
  if (path.length === 0) return [0, 0];
  if (path.length === 1) return path[0];
  const cum = cumulative(path);
  const L = cum.at(-1)!;
  const t = Math.min(Math.max(s, 0), L);
  for (let i = 0; i < path.length - 1; i++) {
    if (t <= cum[i + 1] || i === path.length - 2) {
      const seg = cum[i + 1] - cum[i];
      const r = seg === 0 ? 0 : (t - cum[i]) / seg;
      const [a, b] = [path[i], path[i + 1]];
      return [a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r];
    }
  }
  return path.at(-1)!;
}

/** Closest point on the path to p: its distance along the path and how far away it is. */
export function project(path: Pt[], p: Pt): { s: number; d: number } {
  let best = { s: 0, d: Infinity };
  const cum = cumulative(path);
  for (let i = 0; i < path.length - 1; i++) {
    const [a, b] = [path[i], path[i + 1]];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const L2 = dx * dx + dy * dy;
    const t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2));
    const q: Pt = [a[0] + dx * t, a[1] + dy * t];
    const d = dist(p, q);
    if (d < best.d) best = { s: cum[i] + t * Math.sqrt(L2), d };
  }
  return best;
}

/** The stretch of path between s0 and s1, as a polyline. */
export function subpath(path: Pt[], s0: number, s1: number): Pt[] {
  const [a, b] = s0 <= s1 ? [s0, s1] : [s1, s0];
  const cum = cumulative(path);
  const out: Pt[] = [pointAt(path, a)];
  for (let i = 1; i < path.length - 1; i++) if (cum[i] > a && cum[i] < b) out.push(path[i]);
  out.push(pointAt(path, b));
  return out;
}

/** [most right, most left] the path ever points from its first leg's direction (left/CCW positive). */
export function headingRangeDeg(path: Pt[]): [number, number] {
  let total = 0;
  let lo = 0;
  let hi = 0;
  let prev: number | null = null;
  for (let i = 1; i < path.length; i++) {
    const h = Math.atan2(path[i][1] - path[i - 1][1], path[i][0] - path[i - 1][0]);
    if (prev !== null) {
      total += Math.atan2(Math.sin(h - prev), Math.cos(h - prev));
      lo = Math.min(lo, total);
      hi = Math.max(hi, total);
    }
    prev = h;
  }
  return [(lo * 180) / Math.PI, (hi * 180) / Math.PI];
}

/** Furthest the path ever points away from its first leg's direction. */
export function maxHeadingDeg(path: Pt[]): number {
  let total = 0;
  let worst = 0;
  let prev: number | null = null;
  for (let i = 1; i < path.length; i++) {
    const h = Math.atan2(path[i][1] - path[i - 1][1], path[i][0] - path[i - 1][0]);
    if (prev !== null) {
      total += Math.atan2(Math.sin(h - prev), Math.cos(h - prev));
      worst = Math.max(worst, Math.abs(total));
    }
    prev = h;
  }
  return (worst * 180) / Math.PI;
}

export function netTurnDeg(path: Pt[]): number {
  let total = 0;
  let prev: number | null = null;
  for (let i = 1; i < path.length; i++) {
    const h = Math.atan2(path[i][1] - path[i - 1][1], path[i][0] - path[i - 1][0]);
    if (prev !== null) total += Math.atan2(Math.sin(h - prev), Math.cos(h - prev));
    prev = h;
  }
  return (total * 180) / Math.PI;
}

export type Check = { level: "error" | "warning" | "ok"; text: string };

export function checkCourse(c: Course, deck: DeckSettings = DEFAULT_DECK): { checks: Check[]; length: number; runTimeS: number; peakRps: number } {
  const checks: Check[] = [];
  const length = pathLength(c.path);
  const v = c.walker.speed_mps / c.rig.time_scale;
  const curbs = c.objects.filter((o) => o.type === "curb").length;
  const runTimeS = c.path.length >= 2 ? SETUP_S + length / v + curbs * CURB_PAUSE_S : 0;
  const r = Math.max(c.corner_radius_m, 0.05);
  const turns = c.path.length >= 3;
  const peakV = turns ? v * (1 + c.walker.track_m / (2 * r)) : v;
  const peakRps = peakV / c.walker.wheel_circ_m;

  if (c.path.length < 2) checks.push({ level: "error", text: "Draw a path with at least two points." });
  const maxRps = deck.wheel_max_rps ?? WHEEL_MAX_RPS;
  if (peakRps > maxRps) {
    const k = (c.rig.time_scale * peakRps) / maxRps;
    checks.push({
      level: "error",
      text: `Wheels would need ${peakRps.toFixed(2)} turns/s in corners (${deck.wheel_max_rps ? "calibrated" : "servo"} limit about ${maxRps.toFixed(2)}). Raise the time scale to at least ${Math.ceil(k * 10) / 10}, or increase the corner radius.`,
    });
  }
  const lowRps = turns ? Math.max(0, v * (1 - c.walker.track_m / (2 * r))) / c.walker.wheel_circ_m : peakRps;
  if (deck.wheel_min_rps && turns && lowRps < deck.wheel_min_rps - 0.01)
    checks.push({
      level: "warning",
      text: `The inner wheel needs ${lowRps.toFixed(2)} turns/s in corners, below the slowest your servos run smoothly (${deck.wheel_min_rps.toFixed(2)}), so it will stutter or stop there. Increase the corner radius.`,
    });
  // Only heading changes matter to the IMU: if the turns don't fit one side of the (trimmed) yaw servo,
  // the compiler starts the deck turned the other way. Mirrors pipeline/rig/compiler.py.
  const [needLo, needHi] = headingRangeDeg(c.path);
  const [canLo, canHi] = yawRange(deck);
  const yawStart = needHi > canHi ? canHi - needHi : needLo < canLo ? canLo - needLo : 0;
  if (needHi - needLo > canHi - canLo + 0.5) {
    checks.push({
      level: "error",
      text: `The path turns through ${Math.round(needHi - needLo)}° in total (${Math.round(needHi)}° left and ${Math.round(-needLo)}° right of its first leg); the yaw servo covers ${Math.round(canHi - canLo)}°. Keep the legs within that (a right turn needs a left turn before another right).`,
    });
  } else if (Math.abs(yawStart) > 0.5) {
    checks.push({
      level: "warning",
      text: `The deck will start turned ${Math.round(Math.abs(yawStart))}° ${yawStart > 0 ? "left" : "right"} so the turns fit the trimmed yaw servo (data/deck.json). That's fine: headings are measured from the start.`,
    });
  }
  for (const o of c.objects) {
    if (o.type === "slope" || o.type === "cross") {
      const pct = o.type === "slope" ? o.grade_pct : o.tilt_pct;
      const deg = (Math.atan(Math.abs(pct) / 100) * 180) / Math.PI;
      if (deg > SERVO_LIMIT_DEG)
        checks.push({ level: "warning", text: `${o.type === "slope" ? "Slope" : "Cross-slope"} of ${pct}% exceeds the servo range and will be clipped to about 46%.` });
    }
    const end = "s" in o ? o.s : Math.max(o.s0, o.s1);
    if (end > length + 1e-6) checks.push({ level: "warning", text: `A ${o.type} sits past the end of the path.` });
  }
  if (c.objects.some((o) => o.type === "cross"))
    checks.push({ level: "warning", text: "Cross-slope needs the optional roll servo on pin 11." });
  if (!checks.some((x) => x.level !== "ok")) checks.push({ level: "ok", text: "Ready to run." });
  return { checks, length, runTimeS, peakRps };
}

export function objectLabel(o: CourseObject): string {
  switch (o.type) {
    case "crack": return `Crack (${o.severity})`;
    case "curb": return `Curb (${o.height})`;
    case "slope": return `Slope ${o.grade_pct}%`;
    case "cross": return `Cross-slope ${o.tilt_pct}%`;
    case "rough": return `Rough (${o.level})`;
  }
}

/* im fucking balding */
export const EXAMPLE_COURSE: Course = {
  name: "L-loop with curb",
  walker: { speed_mps: 0.8, wheel_circ_m: 0.64, track_m: 0.5 },
  rig: { time_scale: 2.5 },
  corner_radius_m: 1.0,
  path: [[3, 3], [3, 11], [13, 11], [13, 20], [24, 20]],
  objects: [
    { id: "c1", type: "crack", s: 4.0, severity: "large" },
    { id: "k1", type: "curb", s: 13.0, height: "high" },
    { id: "p1", type: "slope", s0: 20.0, s1: 25.0, grade_pct: 8 },
    { id: "r1", type: "rough", s0: 28.0, s1: 31.0, level: "light" },
    { id: "c2", type: "crack", s: 33.5, severity: "small" },
  ],
};

export const EMPTY_COURSE: Course = {
  ...EXAMPLE_COURSE,
  name: "Untitled course",
  path: [],
  objects: [],
};
