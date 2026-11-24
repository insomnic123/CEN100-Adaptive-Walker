export type Pt = [number, number];

export type Crack = { id: string; type: "crack"; s: number; severity: "small" | "large" };
export type Curb = { id: string; type: "curb"; s: number; height: "low" | "high" };
export type Slope = { id: string; type: "slope"; s0: number; s1: number; grade_pct: number };
export type Cross = { id: string; type: "cross"; s0: number; s1: number; tilt_pct: number };
export type Rough = { id: string; type: "rough"; s0: number; s1: number; level: "light" | "heavy" };

export type PointObject = Crack | Curb;
export type ZoneObject = Slope | Cross | Rough;
export type CourseObject = PointObject | ZoneObject;
export type ObjectType = CourseObject["type"];

export type Course = {
  name: string;
  walker: { speed_mps: number; wheel_circ_m: number; track_m: number };
  rig: { time_scale: number };
  corner_radius_m: number;
  path: Pt[];
  objects: CourseObject[];
};

export type RunStatus = "queued" | "running" | "done" | "error" | "aborted";

export type Run = {
  id: number;
  status: RunStatus;
  progress_s: number;
  duration_s: number | null;
  message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export const isZone = (o: CourseObject): o is ZoneObject =>
  o.type === "slope" || o.type === "cross" || o.type === "rough";

// ---- results.json (pipeline/rig/score.py)
export type ResultStatus = "found" | "missed" | "wrong type";
export type ResultObject = {
  id?: string;
  type: CourseObject["type"];
  label: string;
  status: ResultStatus;
  t?: number;
  t0?: number;
  t1?: number;
  timing_ms?: number;
  note?: string;
};
export type Detection = { kind: string; t?: number; t0?: number; t1?: number; severity?: string; level?: string; grade_pct?: number };
export type RunResults =
  | { ok: false; message: string }
  | {
      ok: true;
      summary: {
        objects: number;
        found: number;
        wrong_type: number;
        missed: number;
        false_alarms: number;
        mean_timing_ms: number | null;
        distance_m: number | null;
        route_m: number;
        distance_error_pct: number | null;
      };
      objects: ResultObject[];
      false_alarms: Detection[];
      imu: { fake: boolean; mount_tilt_deg: number };
      path: Pt[];
      course_path: Pt[];
      traces: { t: number[]; tilt_deg: number[]; shake_dps: number[] };
    };
