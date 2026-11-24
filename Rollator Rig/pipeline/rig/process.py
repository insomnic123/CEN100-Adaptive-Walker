"""Turns a run's sensor log into detections: what the kit felt, and when.

Input (a run folder): imu.csv, wheels.csv, sensor_meta.json (rig/sensor.py), truth.json and
timeline.csv (the compiler). Nothing here assumes how the IMU is mounted:
  - "up" is gravity during the standstill at the start (which also cancels any mounting tilt),
  - the tilt axis is the direction of the sync jolt (a flick of the tilt servo at t = 5 s),
  - the clocks are lined up by the laptop's receive times, then refined on the sync jolt.
Detections:
  - jolts (cracks, curbs): short, fast flicks about the tilt axis, sized against the sync jolt
    (10 deg): small crack ~0.6x, large crack ~1.2x, curb ~1.8x (and the wheels stop first),
  - rough patches: a run of 5+ quick flicks back and forth,
  - slopes: the deck held tilted (from gravity) for over a second,
  - cross-slopes: the same about the other horizontal axis (needs the roll servo),
  - distance from the wheel notches (4 per turn), heading from the gyro, and the path from both.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
from pathlib import Path

from .sensor import ACC_LSB, GYRO_LSB

STANDSTILL = (1.0, 4.5)       # rig seconds: still, for gravity + gyro offset
SYNC_WINDOW = (4.0, 6.5)      # the sync jolt is commanded at 5.0 s
HOLES = 4


class ProcessError(Exception):
    pass


# ---------------------------------------------------------------- small vector helpers
def _add(a, b): return [x + y for x, y in zip(a, b)]
def _sub(a, b): return [x - y for x, y in zip(a, b)]
def _mul(a, k): return [x * k for x in a]
def _dot(a, b): return sum(x * y for x, y in zip(a, b))
def _cross(a, b): return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
def _norm(a): return math.sqrt(_dot(a, a))
def _unit(a):
    n = _norm(a)
    return _mul(a, 1 / n) if n > 1e-12 else a


def _mean_vec(vs):
    n = max(1, len(vs))
    return [sum(v[i] for v in vs) / n for i in range(3)]


def _moving_avg(xs, n):
    if n <= 1:
        return list(xs)
    out, acc, half = [], 0.0, n // 2
    pref = [0.0]
    for x in xs:
        acc += x
        pref.append(acc)
    for i in range(len(xs)):
        lo, hi = max(0, i - half), min(len(xs), i + half + 1)
        out.append((pref[hi] - pref[lo]) / (hi - lo))
    return out


# ---------------------------------------------------------------- loading + clock alignment
def load(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    if not (run_dir / "imu.csv").exists():
        raise ProcessError("no sensor log (imu.csv) for this run: was the ESP32 recorded (--esp-port)?")
    meta = json.loads((run_dir / "sensor_meta.json").read_text()) if (run_dir / "sensor_meta.json").exists() else {}
    host, esp, acc, gyr = [], [], [], []
    for r in csv.DictReader(open(run_dir / "imu.csv")):
        host.append(float(r["host_t"]))
        esp.append(int(r["esp_ms"]))
        acc.append([int(r["ax"]) / ACC_LSB, int(r["ay"]) / ACC_LSB, int(r["az"]) / ACC_LSB])
        gyr.append([int(r["gx"]) / GYRO_LSB, int(r["gy"]) / GYRO_LSB, int(r["gz"]) / GYRO_LSB])
    if len(esp) < 200:
        raise ProcessError(f"only {len(esp)} IMU samples recorded: is sensor_esp32 uploaded and the IMU wired?")
    wheels = [{k: (float(v) if k == "host_t" else int(v)) for k, v in r.items()}
              for r in csv.DictReader(open(run_dir / "wheels.csv"))] if (run_dir / "wheels.csv").exists() else []
    truth = json.loads((run_dir / "truth.json").read_text())
    timeline = [{k: float(v) for k, v in r.items()} for r in csv.DictReader(open(run_dir / "timeline.csv"))]
    return {"host": host, "esp": esp, "acc": acc, "gyr": gyr, "wheels": wheels, "meta": meta,
            "truth": truth, "timeline": timeline}


def clock(d: dict) -> tuple[float, str]:
    """Rig seconds = esp_ms / 1000 + shift. Uses the fastest-arriving lines (least USB delay)."""
    hb = d["meta"].get("host_begin")
    if hb is None:
        return None, "none"
    shift = min(h - e / 1000 for h, e in zip(d["host"], d["esp"])) - hb
    return shift, "laptop clock"


# ---------------------------------------------------------------- lobes (one-way flicks) of a rate signal
def lobes(t, rate, thresh):
    """Stretches where |rate| > thresh with one sign: start, end, sign, peak, area (deg)."""
    out, cur = [], None
    for i, (ti, r) in enumerate(zip(t, rate)):
        s = 1 if r > thresh else -1 if r < -thresh else 0
        if cur and (s != cur["sign"]):
            cur["end"] = t[i - 1]
            out.append(cur)
            cur = None
        if s and cur is None:
            cur = {"start": ti, "end": ti, "sign": s, "peak": 0.0, "area": 0.0, "i0": i}
        if cur:
            cur["peak"] = max(cur["peak"], abs(r))
            dt = t[i] - t[i - 1] if i else 0.005
            cur["area"] += r * dt
    if cur:
        cur["end"] = t[-1]
        out.append(cur)
    return out


# ---------------------------------------------------------------- main
def process(run_dir: Path) -> dict:
    d = load(run_dir)
    truth = d["truth"]
    sync_at = float(truth.get("sync_jolt_s", 5.0))
    shift, how = clock(d)
    t = [e / 1000 + shift for e in d["esp"]] if shift is not None else None

    # if the laptop clock is missing, find the sync jolt as the first big flick in the log
    if t is None:
        t0 = d["esp"][0] / 1000
        t = [e / 1000 - t0 for e in d["esp"]]
        mags = [_norm(g) for g in d["gyr"]]
        base = sorted(mags[:200])[100]
        k = next((i for i, m in enumerate(mags) if m > base + 100), None)
        if k is None:
            raise ProcessError("no sync jolt found and no laptop clock to line the log up with")
        t = [x - t[k] + sync_at for x in t]
        how = "sync jolt only"

    still = [i for i, x in enumerate(t) if STANDSTILL[0] <= x <= STANDSTILL[1]]
    if len(still) < 100:
        raise ProcessError("the sensor log doesn't cover the standstill at the start of the run "
                           "(start recording before the run, and check the ESP32 isn't resetting)")
    g0 = _mean_vec([d["acc"][i] for i in still])
    bias = _mean_vec([d["gyr"][i] for i in still])
    up = _unit(g0)
    tilt_at_rest = math.degrees(math.acos(max(-1.0, min(1.0, up[2]))))   # vs the board's own z

    w = [_sub(g, bias) for g in d["gyr"]]
    wmag = [_norm(v) for v in w]
    noise = math.sqrt(sum(wmag[i] ** 2 for i in still) / len(still))

    # ---- the sync jolt: refines the clock and gives the tilt axis and the size of a 10-deg flick
    win = [i for i, x in enumerate(t) if SYNC_WINDOW[0] <= x <= SYNC_WINDOW[1]]
    if not win:
        raise ProcessError("the sensor log doesn't cover the sync jolt at 5 s")
    peak_i = max(win, key=lambda i: wmag[i])
    peak = wmag[peak_i]
    if peak < max(30.0, 8 * noise):
        raise ProcessError(f"no sync jolt felt near {sync_at:.0f} s (biggest turn rate {peak:.0f} deg/s): "
                           "is the IMU on the deck, and did the tilt servo flick?")
    first = next(i for i in win if wmag[i] > 0.3 * peak)             # onset of the first flick
    k = first
    while k + 1 < len(wmag) and wmag[k + 1] >= wmag[k]:              # its first maximum
        k += 1
    axis = _sub(w[k], _mul(up, _dot(w[k], up)))                       # tilt axis, horizontal
    axis = _unit(axis)
    fwd = _unit(_cross(axis, up))                                     # the other horizontal axis
    offset = t[first] - sync_at
    t = [x - offset for x in t]

    rate = [_dot(v, axis) for v in w]                                 # tilt rate, deg/s (+ = like the sync flick)
    yaw_rate = [_dot(v, up) for v in w]
    sync_lobes = [l for l in lobes(t, rate, 0.1 * peak) if sync_at - 0.2 <= l["start"] <= sync_at + 0.3]
    sync_area = max((l["area"] for l in sync_lobes if l["sign"] > 0), default=0.0)
    if sync_area <= 1.0:
        raise ProcessError("couldn't measure the sync jolt's size")

    # ---- tilt from gravity (smoothed), relative to the standstill
    n_smooth = 60                                                     # 0.3 s at 200 Hz
    sm = [_moving_avg([a[i] for a in d["acc"]], n_smooth) for i in range(3)]
    g_s = [[sm[0][i], sm[1][i], sm[2][i]] for i in range(len(t))]
    def tilt_about(ax):
        out = []
        for g in g_s:
            c = _cross(g, g0)
            out.append(math.degrees(math.atan2(_dot(c, ax), _dot(g, g0))))
        return out
    pitch = tilt_about(axis)
    roll = tilt_about(fwd)

    # ---- flicks: jolts and rough patches
    all_lobes = [l for l in lobes(t, rate, max(0.1 * peak, 6 * noise)) if l["start"] > sync_at + 0.5]
    big = [l for l in all_lobes if abs(l["area"]) >= 0.15 * sync_area]
    clusters, cur = [], []
    for l in big:
        if cur and l["start"] - cur[-1]["end"] > 0.25:
            clusters.append(cur)
            cur = []
        cur.append(l)
    if cur:
        clusters.append(cur)

    detections = []
    busy = []                                                         # times when gravity is unreliable
    wheel_t, wheel_s = wheel_distance(d, shift, offset, truth)
    for c in clusters:
        t0, t1 = c[0]["start"], c[-1]["end"]
        busy.append((t0 - 0.3, t1 + 0.3))
        if len(c) >= 5:
            sizes = sorted(abs(l["area"]) for l in c)
            ratio = sizes[len(sizes) // 2] / sync_area
            detections.append({"kind": "rough", "t0": round(t0, 2), "t1": round(t1, 2),
                               "level": "heavy" if ratio >= 0.45 else "light",
                               "size_ratio": round(ratio, 2), "flicks": len(c)})
            continue
        pos = [l for l in c if l["sign"] > 0] or c
        main = max(pos, key=lambda l: l["peak"])                     # the fast flick, not a slow nod
        ratio = abs(main["area"]) / sync_area
        stopped = wheels_stopped_before(wheel_t, wheel_s, main["start"])
        if ratio >= 1.5 or (stopped and ratio >= 1.1):
            kind, size = "curb", None
        elif ratio >= 0.9:
            kind, size = "crack", "large"
        else:
            kind, size = "crack", "small"
        at = main["start"]
        if kind == "curb":                                            # a curb starts with its pause
            at = wheels_stop_time(wheel_t, wheel_s, main["start"]) or main["start"] - 0.5
        det = {"kind": kind, "t": round(at, 2), "size_ratio": round(ratio, 2),
               "deg": round(abs(main["area"]) * 10 / sync_area, 1), "wheels_stopped": stopped}
        if size:
            det["severity"] = size
        detections.append(det)

    # ---- slopes and cross-slopes: held tilt for over a second, outside flicks
    def held(sig, kind, thresh=1.5, min_s=1.0):
        found, start = [], None
        for i, x in enumerate(t):
            ok = abs(sig[i]) > thresh and not any(a <= x <= b for a, b in busy) and x > sync_at + 1.0
            if ok and start is None:
                start = i
            if (not ok or i == len(t) - 1) and start is not None:
                if t[i] - t[start] >= min_s:
                    seg = sig[start:i]
                    mid = sorted(seg)[len(seg) // 2]
                    found.append({"kind": kind, "t0": round(t[start], 2), "t1": round(t[i], 2),
                                  "deg": round(mid, 1), "grade_pct": round(math.tan(math.radians(mid)) * 100, 1)})
                start = None
        merged = []
        for f in found:                                               # join pieces split by a flick
            if merged and f["t0"] - merged[-1]["t1"] < 1.0 and (f["deg"] > 0) == (merged[-1]["deg"] > 0):
                merged[-1]["t1"] = f["t1"]
            else:
                merged.append(f)
        return merged
    detections += held(pitch, "slope") + held(roll, "cross")
    detections.sort(key=lambda x: x.get("t", x.get("t0")))

    # ---- distance, heading and path
    heading = []
    h = 0.0
    move_at = float(truth.get("move_s", 6.0))
    for i in range(len(t)):
        if i:
            h += yaw_rate[i] * (t[i] - t[i - 1])
        heading.append(h)
    h_move = heading[bisect.bisect_left(t, move_at)] if t[-1] > move_at else 0.0
    heading = [x - h_move for x in heading]
    sign = heading_sign(t, heading, d["timeline"], truth)
    heading = [sign * x for x in heading]
    path = dead_reckon(t, heading, wheel_t, wheel_s, truth)

    step = 20                                                         # traces at 10 Hz for plotting
    shake = _moving_avg([abs(r) for r in rate], 40)
    traces = {"t": [round(t[i], 2) for i in range(0, len(t), step)],
              "tilt_deg": [round(pitch[i], 2) for i in range(0, len(t), step)],
              "cross_deg": [round(roll[i], 2) for i in range(0, len(t), step)],
              "shake_dps": [round(max(shake[i:i + step]), 1) for i in range(0, len(t), step)],
              "heading_deg": [round(heading[i], 1) for i in range(0, len(t), step)]}
    return {
        "alignment": {"clock": how, "sync_offset_ms": round(offset * 1000, 1)},
        "imu": {"id": d["meta"].get("imu_id"), "samples": len(t), "rate_hz": round(len(t) / (t[-1] - t[0]), 1),
                "mount_tilt_deg": round(tilt_at_rest, 1), "gyro_offset_dps": [round(x, 2) for x in bias],
                "noise_dps": round(noise, 2), "sync_peak_dps": round(peak, 1), "sync_deg": round(sync_area, 1),
                "fake": bool(d["meta"].get("fake"))},
        "detections": detections,
        "distance_m": round(wheel_s[-1], 2) if wheel_s else None,
        "path": path,
        "traces": traces,
    }


# ---------------------------------------------------------------- wheels, heading, path
def wheel_distance(d, shift, offset, truth):
    circ = float(truth["course"].get("walker", {}).get("wheel_circ_m", 0.64))
    ts, ss = [], []
    if not d["wheels"] or shift is None:
        return ts, ss
    l0, r0 = d["wheels"][0]["left"], d["wheels"][0]["right"]
    for w in d["wheels"]:
        ts.append(w["esp_ms"] / 1000 + shift - offset)
        ss.append(((w["left"] - l0) + (w["right"] - r0)) / 2 / HOLES * circ)
    return ts, ss


def wheels_stopped_before(wt, ws, at, span=0.8):
    """True if the wheels didn't move in the span seconds before a flick (a curb's pause)."""
    if len(wt) < 3:
        return False
    i0, i1 = bisect.bisect_left(wt, at - span), bisect.bisect_left(wt, at)
    if i1 - i0 < 3 or i1 >= len(ws):
        return False
    return ws[i1] - ws[i0] < 0.02 and ws[i1] > 0.1


def wheels_stop_time(wt, ws, at, look=1.5):
    """When the wheels last stopped before `at` (within `look` s), or None."""
    i1 = bisect.bisect_left(wt, at)
    i0 = bisect.bisect_left(wt, at - look)
    if i1 - i0 < 3 or i1 >= len(ws):
        return None
    for i in range(i1, i0, -1):
        if ws[i] - ws[i - 1] > 0.005:                                 # still moving here
            return wt[i] if i < i1 else None
    return None


def heading_sign(t, heading, timeline, truth) -> int:
    ys = float(truth.get("yaw_sign", -1)) or -1
    ystart = float(truth.get("yaw_start_deg", 0))
    ts = [r["t_ms"] / 1000 for r in timeline]
    cmd = [(r["yaw_deg"] - 90) / ys - ystart for r in timeline]
    acc = 0.0
    for i in range(0, len(t), 20):
        j = min(bisect.bisect_left(ts, t[i]), len(cmd) - 1)
        acc += heading[i] * (cmd[j] - cmd[0])
    return -1 if acc < 0 else 1


def dead_reckon(t, heading, wt, ws, truth):
    pts = truth["course"].get("path") or [[0, 0], [1, 0]]
    x, y = pts[0]
    h0 = math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]) if len(pts) > 1 else 0.0
    out = [[round(x, 2), round(y, 2)]]
    for i in range(1, len(wt)):
        ds = ws[i] - ws[i - 1]
        if ds <= 0:
            continue
        k = min(bisect.bisect_left(t, wt[i]), len(heading) - 1)
        h = h0 + math.radians(heading[k])
        x, y = x + ds * math.cos(h), y + ds * math.sin(h)
        out.append([round(x, 2), round(y, 2)])
    if len(out) > 400:
        step = len(out) // 400 + 1
        out = out[::step] + [out[-1]]
    return out
