import type { Course } from "./types";

const num = (v: unknown) => typeof v === "number" && Number.isFinite(v);

/** Shape check for courses arriving at the API (the editor is trusted, but files can be hand-edited). */
export function isCourse(c: unknown): c is Course {
  if (!c || typeof c !== "object") return false;
  const x = c as Course;
  return (
    typeof x.name === "string" &&
    num(x.walker?.speed_mps) && x.walker.speed_mps > 0 &&
    num(x.walker?.wheel_circ_m) && x.walker.wheel_circ_m > 0 &&
    num(x.walker?.track_m) &&
    num(x.rig?.time_scale) && x.rig.time_scale >= 1 &&
    num(x.corner_radius_m) &&
    Array.isArray(x.path) &&
    x.path.every((p) => Array.isArray(p) && p.length === 2 && num(p[0]) && num(p[1])) &&
    Array.isArray(x.objects) &&
    x.objects.every((o) => o && typeof o.type === "string" && typeof o.id === "string")
  );
}
