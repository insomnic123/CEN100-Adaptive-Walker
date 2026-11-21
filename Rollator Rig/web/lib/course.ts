// Course geometry. Mirrors pipeline/rig/course.py: keep the two in step.
import type { Course, CourseObject, Pt } from "./types";

export const STEPS_PER_REV = 4096;
export const MAX_WHEEL_SPS = 1000;
export const SERVO_LIMIT_DEG = 25;
export const SETUP_S = 6; // Time to get the walker moving and the rig ready before the course starts
export const CURB_PAUSE_S = 1;

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

export function checkCourse(c: Course): { checks: Check[]; length: number; runTimeS: number; peakSps: number } {
  const checks: Check[] = [];
  const length = pathLength(c.path);
  const v = c.walker.speed_mps / c.rig.time_scale;
  const curbs = c.objects.filter((o) => o.type === "curb").length;
  const runTimeS = c.path.length >= 2 ? SETUP_S + length / v + curbs * CURB_PAUSE_S : 0;
  const r = Math.max(c.corner_radius_m, 0.05);
  const turns = c.path.length >= 3;
  const peakV = turns ? v * (1 + c.walker.track_m / (2 * r)) : v;
  const peakSps = (peakV / c.walker.wheel_circ_m) * STEPS_PER_REV;

  if (c.path.length < 2) checks.push({ level: "error", text: "Draw a path with at least two points." });
  if (peakSps > MAX_WHEEL_SPS) {
    const k = (c.rig.time_scale * peakSps) / MAX_WHEEL_SPS;
    checks.push({
      level: "error",
      text: `Wheels would need ${Math.round(peakSps)} steps/s in corners (limit ${MAX_WHEEL_SPS}). Raise the time scale to at least ${Math.ceil(k * 10) / 10}, or increase the corner radius.`,
    });
  }
  const turn = netTurnDeg(c.path);
  if (Math.abs(turn) > 360)
    checks.push({ level: "error", text: `Path turns ${Math.round(turn)}° in total; keep it within ±360° so the turntable wires don't wind up.` });
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
  return { checks, length, runTimeS, peakSps };
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
  rig: { time_scale: 8 },
  corner_radius_m: 0.5,
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
