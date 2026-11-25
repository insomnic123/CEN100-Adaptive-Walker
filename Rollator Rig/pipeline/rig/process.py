"""Turns a run's sensor log into detections: what the kit felt, and when.

Input (a run folder): imu.csv, wheels.csv, sensor_meta.json (rig/sensor.py), truth.json and
timeline.csv (the compiler). Nothing here assumes how the IMU is mounted:
  - "up" is gravity during the standstill at the start (which also cancels any mounting tilt),
  - the yaw axis is learned from the gyro while the deck turns; a leaning yaw stack would otherwise
    make every corner look like a slope, so the deck's yaw is undone before tilt is measured,
  - the tilt axis is the direction of the sync jolt (a flick of the tilt servo at t = 5 s),
  - the clocks are lined up by the laptop's receive times, then refined on the sync jolt.
Detections:
  - jolts (cracks, curbs): short, fast flicks about the tilt axis, sized against the sync jolt
    (10 deg): small crack ~0.6x, large crack ~1.2x, curb ~1.8x (and the wheels stop first),
  - rough patches: 1.5 s+ of shaking with the rig's rough rhythm (a flick every 80 ms),
  - slopes: the deck held tilted 2.5 deg+ (from gravity) for over a second,
  - cross-slopes: 3 deg+ about the other horizontal axis for 2 s (needs the roll servo),
  - flicks, tilts and shaking while the deck is turning are skipped: that's the rig rattling,
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


# ---------------------------------------------------------------- rig geometry: the yaw axis
def _rot(v, k, ang):
    """v rotated by ang (radians) about unit axis k (Rodrigues)."""
    c, s = math.cos(ang), math.sin(ang)
    kxv, kv = _cross(k, v), _dot(k, v)
    return [v[i] * c + kxv[i] * s + k[i] * kv * (1 - c) for i in range(3)]


def yaw_axis(t, w, timeline, up):
    """The yaw servo's real axis in the IMU's frame, from the gyro while the deck turns (and the tilt
    servo is still). A cardboard stack can lean 20+ deg off vertical; turning about a leaning axis tips
    the IMU, which would read as slopes at every corner unless it's undone."""
    tt = [r["t_ms"] / 1000 for r in timeline]
    acc, n = [0.0, 0.0, 0.0], 0
    for i in range(0, len(t), 2):
        j = min(bisect.bisect_left(tt, t[i]), len(timeline) - 1)
        j0, j1 = max(j - 5, 0), min(j + 5, len(timeline) - 1)
        dy = (timeline[j1]["yaw_deg"] - timeline[j0]["yaw_deg"]) / max(1e-3, tt[j1] - tt[j0])
        if abs(dy) < 10 or abs(timeline[j1]["pitch_deg"] - timeline[j0]["pitch_deg"]) >= 1:
            continue
        if any(timeline[x]["jolt"] or timeline[x]["buzz"] for x in range(j0, j1 + 1)):
            continue
        acc = _add(acc, _mul(w[i], 1 if dy > 0 else -1))
        n += 1
    if n < 50:
        return up, 0.0
    k = _unit(acc)
    if _dot(k, up) < 0:
        k = _mul(k, -1)
    return k, math.degrees(math.acos(max(-1.0, min(1.0, _dot(k, up)))))


def yaw_angle(t, w, k, still, timeline):
    """Deck yaw (radians) from the gyro. Drift is removed twice: whenever the rig holds the deck at any
    yaw command, the true yaw isn't changing, so the gyro's average rate there is its drift (measured
    per hold and interpolated between holds); then the angle is re-anchored to 0 whenever the deck has
    sat at its starting yaw command for a second."""
    tt0 = [r["t_ms"] / 1000 for r in timeline]
    yc = [r["yaw_deg"] for r in timeline]
    rate_k = [_dot(v, k) for v in w]
    holds = []                                                        # (mid time, drift deg/s)
    j = 0
    while j < len(yc):
        j1 = j
        while j1 + 1 < len(yc) and yc[j1 + 1] == yc[j]:
            j1 += 1
        a0, a1 = tt0[j] + 0.7, tt0[j1]                                # skip 0.7 s of settling
        if a1 - a0 >= 1.0:
            i0, i1 = bisect.bisect_left(t, a0), bisect.bisect_left(t, a1)
            if i1 - i0 > 50:
                seg = sorted(rate_k[i0:i1])
                holds.append(((a0 + a1) / 2, seg[len(seg) // 2]))     # median: ignores jolts
        j = j1 + 1
    def drift_rate(x):
        if not holds:
            return 0.0
        n = bisect.bisect_left([h[0] for h in holds], x)
        if n == 0:
            return holds[0][1]
        if n >= len(holds):
            return holds[-1][1]
        (x0, r0), (x1, r1) = holds[n - 1], holds[n]
        return r0 + (r1 - r0) * (x - x0) / (x1 - x0)
    psi = [0.0]
    for i in range(1, len(t)):
        psi.append(psi[-1] + math.radians(rate_k[i] - drift_rate(t[i])) * (t[i] - t[i - 1]))
    z = sum(psi[i] for i in still) / len(still)
    psi = [x - z for x in psi]
    tt = [r["t_ms"] / 1000 for r in timeline]
    ycmd = [r["yaw_deg"] for r in timeline]
    ystart = ycmd[min(bisect.bisect_left(tt, 3.0), len(ycmd) - 1)]
    at, av = [], []
    for i in range(0, len(t), 20):
        if t[i] < 1.0 or t[i] > tt[-1]:
            continue
        j = min(bisect.bisect_left(tt, t[i]), len(ycmd) - 1)
        j0 = bisect.bisect_left(tt, t[i] - 1.0)
        if all(abs(ycmd[x] - ystart) < 0.5 for x in range(j0, j + 1)):
            at.append(t[i])
            av.append(psi[i])
    if not at:
        return psi
    out = []
    for i, x in enumerate(t):
        n = bisect.bisect_left(at, x)
        if n == 0:
            d0 = av[0]
        elif n >= len(at):
            d0 = av[-1]
        else:
            d0 = av[n - 1] + (av[n] - av[n - 1]) * (x - at[n - 1]) / (at[n] - at[n - 1])
        out.append(psi[i] - d0)
    return out


def rhythm_windows(t, x, f=6.25, fs=200.0, n=200, step=100):
    """Per 1 s window (every 0.5 s): the share of the signal's power at the rig's rough-patch rhythm
    (a flick every 80 ms = 6.25 Hz, allowing +-8 %), and its RMS."""
    out = []
    for i0 in range(0, len(x) - n, step):
        seg = x[i0:i0 + n]
        tot = sum(v * v for v in seg)
        best = 0.0
        for ff in (f * 0.92, f, f * 1.08):
            c = math.cos(2 * math.pi * ff / fs)
            s1 = s2 = 0.0
            for v in seg:
                s0 = v + 2 * c * s1 - s2
                s2, s1 = s1, s0
            pw = s1 * s1 + s2 * s2 - 2 * c * s1 * s2
            best = max(best, 2 * pw / (n * tot) if tot > 1e-9 else 0.0)
        out.append((t[i0], t[min(i0 + n, len(t) - 1)], best, math.sqrt(tot / n)))
    return out


# ---------------------------------------------------------------- main
def process(run_dir: Path) -> dict:
    d = load(run_dir)
    truth = d["truth"]
    sync_at = float(truth.get("sync_jolt_s", 5.0))
    run_end = d["timeline"][-1]["t_ms"] / 1000 if d["timeline"] else 1e9
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
    kk = first
    while kk + 1 < len(wmag) and wmag[kk + 1] >= wmag[kk]:           # its first maximum
        kk += 1
    offset = t[first] - sync_at
    t = [x - offset for x in t]

    # ---- rig geometry: the yaw axis (may lean), the tilt axis (square to it), the other axis
    kax, lean = yaw_axis(t, w, d["timeline"], up)
    axis = _unit(_sub(w[kk], _mul(kax, _dot(w[kk], kax))))
    fwd = _unit(_cross(axis, kax))
    psi = yaw_angle(t, w, kax, still, d["timeline"])

    rate = [_dot(v, axis) for v in w]                                 # tilt rate, deg/s (+ = like the sync flick)
    yaw_rate = [_dot(v, kax) for v in w]
    sync_lobes = [l for l in lobes(t, rate, 0.1 * peak) if sync_at - 0.2 <= l["start"] <= sync_at + 0.3]
    sync_area = max((l["area"] for l in sync_lobes if l["sign"] > 0), default=0.0)
    if sync_area <= 1.0:
        raise ProcessError("couldn't measure the sync jolt's size")

    # times the rig turns the deck (from its own yaw commands, plus 2 s for the cardboard to settle):
    # the yaw servo's rattle and the stack settling aren't the course, so detections skip them
    turning = []
    tl = d["timeline"]
    for j in range(1, len(tl)):
        if tl[j]["yaw_deg"] != tl[j - 1]["yaw_deg"]:
            x = tl[j]["t_ms"] / 1000
            if turning and x - turning[-1][1] < 0.6:
                turning[-1][1] = x
            else:
                turning.append([x, x])
    turning = [(a - 0.5, b + 2.0) for a, b in turning]
    def in_spans(x, spans):
        return any(a <= x <= b for a, b in spans)

    # ---- tilt from gravity (smoothed): measured against where gravity would sit if the deck were
    # level at its current yaw (turning about a leaning axis moves gravity even with no tilt)
    n_smooth = 60                                                     # 0.3 s at 200 Hz
    sm = [_moving_avg([a[i] for a in d["acc"]], n_smooth) for i in range(3)]
    g_s = [[sm[0][i], sm[1][i], sm[2][i]] for i in range(len(t))]
    g_ref = [_rot(g0, kax, -psi[i]) for i in range(len(t))]
    def tilt_about(ax):
        out = []
        for g, r in zip(g_s, g_ref):
            c = _cross(g, r)
            out.append(math.degrees(math.atan2(_dot(c, ax), _dot(g, r))))
        return out
    pitch = tilt_about(axis)
    roll = tilt_about(fwd)

    # ---- rough patches: the rig's rough flicks come every 80 ms, so look for that rhythm
    detections = []
    busy = []                                                         # times when gravity is unreliable
    rough_spans = []
    hp = _moving_avg(rate, 50)
    x = [r - h for r, h in zip(rate, hp)]
    still_rms = math.sqrt(sum(x[i] ** 2 for i in still) / len(still))
    min_rms = max(0.6, 3 * still_rms)                                  # the rhythm test does the real work
    wins = [wv for wv in rhythm_windows(t, x) if sync_at + 1.0 < wv[0] and wv[1] < run_end + 0.5]
    ok = [wv[2] >= 0.2 and wv[3] >= min_rms for wv in wins]
    runs_, cur, gap = [], [], 0
    for wv, good in zip(wins, ok):
        if good:
            cur.append(wv)
            gap = 0
        elif cur:
            gap += 1
            if gap > 1:
                runs_.append(cur)
                cur, gap = [], 0
    if cur:
        runs_.append(cur)
    rough = []
    for r in runs_:
        if len(r) < 3:
            continue
        seg = [r[0][0], r[-1][1], sorted(v[3] for v in r)[len(r) // 2]]
        if rough and seg[0] - rough[-1][1] <= 1.5:
            rough[-1][1] = seg[1]
            rough[-1][2] = max(rough[-1][2], seg[2])
        else:
            rough.append(seg)
    for a, b, level_rms in rough:
        rough_spans.append((a, b))
        detections.append({"kind": "rough", "t0": round(a, 2), "t1": round(b, 2),
                           "level": "heavy" if level_rms >= 0.06 * peak else "light",
                           "shake_dps": round(level_rms, 1)})
        busy.append((a, b))

    # ---- flicks: cracks and curbs (flicks inside a rough patch belong to it)
    all_lobes = [l for l in lobes(t, rate, max(0.1 * peak, 6 * noise))
                 if sync_at + 0.5 < l["start"] < run_end]
    big = [l for l in all_lobes if abs(l["area"]) >= 0.15 * sync_area]
    clusters, cur = [], []
    for l in big:
        if cur and l["start"] - cur[-1]["end"] > 0.25:
            clusters.append(cur)
            cur = []
        cur.append(l)
    if cur:
        clusters.append(cur)

    wheel_t, wheel_s = wheel_distance(d, shift, offset, truth)
    for c in clusters:
        t0, t1 = c[0]["start"], c[-1]["end"]
        busy.append((t0 - 0.3, t1 + 0.3))
        if any(a - 0.3 <= t0 and t1 <= b + 0.3 for a, b in rough_spans):
            continue
        pos = [l for l in c if l["sign"] > 0] or c
        main = max(pos, key=lambda l: l["peak"])                     # the fast flick, not a slow nod
        ratio = abs(main["area"]) / sync_area
        if ratio < 0.4:                                               # rattle, not a jolt
            continue
        stopped = wheels_stopped_before(wheel_t, wheel_s, main["start"])
        # a curb lifts the deck first: a slow nod 0.2 s+ before its jolt (a crack has none)
        nod = any(l["sign"] > 0 and l["start"] <= main["start"] - 0.2 and l["peak"] < 0.6 * main["peak"]
                  and l["area"] >= 0.5 * sync_area for l in c)
        if ratio >= 1.5 or ((stopped or nod) and ratio >= 0.9):
            kind, size = "curb", None
        elif ratio >= 0.9:
            kind, size = "crack", "large"
        else:
            kind, size = "crack", "small"
        at = main["start"]
        if kind == "curb":                                            # a curb starts with its pause
            at = wheels_stop_time(wheel_t, wheel_s, main["start"]) or main["start"] - 0.5
        det = {"kind": kind, "t": round(at, 2), "size_ratio": round(ratio, 2),
               "deg": round(abs(main["area"]) * 10 / sync_area, 1), "wheels_stopped": stopped, "nod": nod}
        if size:
            det["severity"] = size
        detections.append(det)

    # ---- slopes and cross-slopes: held tilt, away from flicks, shaking and turns
    def held(sig, kind, thresh, min_s):
        found, start = [], None
        for i, xx in enumerate(t):
            good = (abs(sig[i]) > thresh and sync_at + 1.0 < xx < run_end
                    and not in_spans(xx, busy) and not in_spans(xx, turning))
            if good and start is None:
                start = i
            if (not good or i == len(t) - 1) and start is not None:
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
    detections += held(pitch, "slope", 2.5, 1.0)
    if truth.get("has_roll_servo"):                                   # no roll servo: nothing to feel
        detections += held(roll, "cross", 3.0, 2.0)
    detections.sort(key=lambda x: x.get("t", x.get("t0")))

    # ---- distance, heading and path
    heading = [math.degrees(p) for p in psi]
    move_at = float(truth.get("move_s", 6.0))
    h_move = heading[bisect.bisect_left(t, move_at)] if t[-1] > move_at else 0.0
    heading = [x - h_move for x in heading]
    sign = heading_sign(t, heading, d["timeline"], truth)
    heading = [sign * x for x in heading]
    path = dead_reckon(t, heading, wheel_t, wheel_s, truth)

    step = 20                                                         # traces at 10 Hz for plotting
    shake = _moving_avg([abs(r) for r in x], 40)
    keep = [i for i in range(0, len(t), step) if t[i] <= run_end + 1.0]
    traces = {"t": [round(t[i], 2) for i in keep],
              "tilt_deg": [round(pitch[i], 2) for i in keep],
              "cross_deg": [round(roll[i], 2) for i in keep],
              "shake_dps": [round(max(shake[i:i + step]), 1) for i in keep],
              "heading_deg": [round(heading[i], 1) for i in keep]}
    return {
        "alignment": {"clock": how, "sync_offset_ms": round(offset * 1000, 1)},
        "imu": {"id": d["meta"].get("imu_id"), "samples": len(t), "rate_hz": round(len(t) / (t[-1] - t[0]), 1),
                "mount_tilt_deg": round(tilt_at_rest, 1), "yaw_axis_lean_deg": round(lean, 1),
                "gyro_offset_dps": [round(x, 2) for x in bias],
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
