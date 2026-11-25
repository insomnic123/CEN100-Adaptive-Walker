"""Compares a run's detections (process.py) with its answer key (truth.json) and writes results.json.

Matching is by time on the run clock:
  - cracks and curbs: the nearest detection of the same family within +-0.6 s,
  - slopes, cross-slopes and rough patches: a detected stretch overlapping at least 30 % of it.
Anything detected that matches nothing is a false alarm.
"""
from __future__ import annotations

import bisect
import csv
import json
from pathlib import Path

from .process import ProcessError, process

POINT_TOL_S = 0.6
ZONE_OVERLAP = 0.3


def _zone_times(timeline, s0, s1):
    """Run-clock times when the route position passes s0 and s1."""
    ss = [r["s"] for r in timeline]
    ts = [r["t_ms"] / 1000 for r in timeline]
    i0 = min(bisect.bisect_left(ss, s0), len(ts) - 1)
    i1 = min(bisect.bisect_left(ss, s1), len(ts) - 1)
    return ts[i0], ts[i1]


def _label(o) -> str:
    t = o["type"]
    if t == "crack":
        return f"{o.get('severity', 'small').capitalize()} crack"
    if t == "curb":
        return f"Curb ({o.get('height', '')})".replace(" ()", "")
    if t == "slope":
        return f"Slope {o.get('grade_pct')}%"
    if t == "cross":
        return f"Cross-slope {o.get('tilt_pct')}%"
    if t == "rough":
        return f"Rough ({o.get('level', 'light')})"
    return t


def score(analysis: dict, truth: dict, timeline: list[dict]) -> dict:
    dets = [dict(d, used=False) for d in analysis["detections"]]
    objects = []
    for o in truth["objects"]:
        row = {"id": o.get("id"), "type": o["type"], "label": _label(o)}
        if o["type"] in ("crack", "curb"):
            tt = o.get("rig_t_s")
            row["t"] = tt
            cands = [d for d in dets if not d["used"] and d["kind"] in ("crack", "curb") and "t" in d
                     and abs(d["t"] - tt) <= POINT_TOL_S]
            if not cands:
                row["status"] = "missed"
            else:
                d = min(cands, key=lambda d: abs(d["t"] - tt))
                d["used"] = True
                row["detected_t"] = d["t"]
                row["timing_ms"] = round((d["t"] - tt) * 1000)
                if d["kind"] != o["type"]:
                    row["status"] = "wrong type"
                    row["note"] = f"felt as a {d['kind']}"
                else:
                    row["status"] = "found"
                    if o["type"] == "crack" and d.get("severity") != o.get("severity", "small"):
                        row["note"] = f"size read as {d.get('severity')}"
        else:
            t0, t1 = _zone_times(timeline, o["route_s0"], o["route_s1"])
            row["t0"], row["t1"] = round(t0, 2), round(t1, 2)
            if o["type"] == "cross" and not truth.get("has_roll_servo"):
                row["status"] = "not tested"
                row["note"] = "needs the roll servo on D11"
                objects.append(row)
                continue
            kind = {"slope": "slope", "cross": "cross", "rough": "rough"}[o["type"]]
            best, best_ov = None, 0.0
            for d in dets:
                if d["used"] or d["kind"] != kind:
                    continue
                ov = max(0.0, min(t1, d["t1"]) - max(t0, d["t0"])) / max(0.1, t1 - t0)
                if ov > best_ov:
                    best, best_ov = d, ov
            if best is None or best_ov < ZONE_OVERLAP:
                row["status"] = "missed"
            else:
                best["used"] = True
                row["status"] = "found"
                row["detected_t0"], row["detected_t1"] = best["t0"], best["t1"]
                row["timing_ms"] = round((best["t0"] - t0) * 1000)
                if o["type"] in ("slope", "cross"):
                    want = float(o.get("grade_pct" if o["type"] == "slope" else "tilt_pct", 0))
                    got = best["grade_pct"] if o["type"] == "slope" else abs(best["grade_pct"]) * (1 if want >= 0 else -1)
                    row["measured_pct"] = got
                    if abs(got - want) > max(2.0, 0.35 * abs(want)):
                        row["note"] = f"measured {got}% (course {want:g}%)"
                elif best.get("level") != o.get("level", "light"):
                    row["note"] = f"read as {best.get('level')}"
        objects.append(row)
    false_alarms = [{k: v for k, v in d.items() if k != "used"} for d in dets if not d["used"]]
    found = sum(r["status"] == "found" for r in objects)
    tested = [r for r in objects if r["status"] != "not tested"]
    timings = [abs(r["timing_ms"]) for r in objects if r.get("timing_ms") is not None and r["status"] == "found"]
    route = float(truth.get("route_length_m", 0))
    dist = analysis.get("distance_m")
    summary = {
        "objects": len(tested), "found": found, "not_tested": len(objects) - len(tested),
        "wrong_type": sum(r["status"] == "wrong type" for r in objects),
        "missed": sum(r["status"] == "missed" for r in objects),
        "false_alarms": len(false_alarms),
        "mean_timing_ms": round(sum(timings) / len(timings)) if timings else None,
        "distance_m": dist, "route_m": route,
        "distance_error_pct": round((dist - route) / route * 100, 1) if dist is not None and route else None,
    }
    return {"ok": True, "summary": summary, "objects": objects, "false_alarms": false_alarms}


def analyse_run(run_dir: Path) -> dict:
    """process + score one run folder; writes and returns results.json (ok=False with a reason on failure)."""
    run_dir = Path(run_dir)
    try:
        a = process(run_dir)
        truth = json.loads((run_dir / "truth.json").read_text())
        with open(run_dir / "timeline.csv") as f:
            timeline = [{k: float(v) for k, v in r.items()} for r in csv.DictReader(f)]
        res = score(a, truth, timeline)
        res.update({k: a[k] for k in ("alignment", "imu", "detections", "path", "traces")})
        res["course_path"] = truth["course"].get("path")
    except ProcessError as e:
        res = {"ok": False, "message": str(e)}
    except Exception as e:                                    # keep the run; report what went wrong
        res = {"ok": False, "message": f"processing failed: {type(e).__name__}: {e}"}
    (run_dir / "results.json").write_text(json.dumps(res, indent=1))
    return res


def summary_line(res: dict) -> str:
    if not res.get("ok"):
        return f"no results: {res.get('message')}"
    s = res["summary"]
    parts = [f"found {s['found']} of {s['objects']}"]
    if s["wrong_type"]:
        parts.append(f"{s['wrong_type']} wrong type")
    if s["false_alarms"]:
        parts.append(f"{s['false_alarms']} false alarm{'s' if s['false_alarms'] != 1 else ''}")
    if s.get("not_tested"):
        parts.append(f"{s['not_tested']} not tested")
    if s["distance_error_pct"] is not None:
        parts.append(f"wheels turned {s['distance_m']} m of {s['route_m']:.1f} m ({s['distance_error_pct']:+.1f}%)")
    return ", ".join(parts)
