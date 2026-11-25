"""Turns a course into a 50 Hz timeline of servo commands, plus the answer key.

Rig: two continuous servos spin the wheels (open loop, from data/calibration.json);
standard servos turn the IMU deck (yaw) and tilt it (pitch, optional roll). Crack and curb
jolts are quick flicks of the pitch servo, done by the firmware when it sees a jolt code.

Every timeline line becomes one serial message to the Arduino:
    T,<t_ms>,<left_us>,<right_us>,<yaw_deg>,<pitch_deg>,<roll_deg>,<jolt>,<buzz>
left_us/right_us = 0 means stop (the firmware detaches that servo so it can't creep).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

from . import calibration, deck as deck_settings
from .course import Course

TICK_S = 0.02                 # 50 Hz timeline
CALIBRATE_S = 5.0             # standstill at the start for tilt + gyro bias
SYNC_AT_S = 5.0               # sync jolt, lines up the kit's clock with this timeline
MOVE_AT_S = 6.0               # wheels start
SERVO_CENTRE = 90
TILT_LIMIT = 25               # pitch / roll servo travel either way, degrees
YAW_LIMIT = 90                # yaw servo: 0-180 degrees = heading -90 .. +90
# yaw direction and deck trims live in data/deck.json (see rig/deck.py)

CURB_PAUSE_S = 1.0
CURB_NOD_DEG = 12.0

# jolt codes understood by the firmware
JOLT_NONE, JOLT_SMALL, JOLT_LARGE, JOLT_CURB, JOLT_SYNC = 0, 1, 2, 3, 4
BUZZ = {"light": 1, "heavy": 2}


@dataclass
class Line:
    t_ms: int
    left_us: int
    right_us: int
    yaw_deg: int
    pitch_deg: int
    roll_deg: int
    jolt: int
    buzz: int
    s: float                  # distance along the route, metres (for progress only)

    def wire(self) -> str:
        return (f"T,{self.t_ms},{self.left_us},{self.right_us},{self.yaw_deg},"
                f"{self.pitch_deg},{self.roll_deg},{self.jolt},{self.buzz}")


@dataclass
class Compiled:
    lines: list[Line]
    truth: dict
    duration_s: float
    errors: list[str]
    warnings: list[str]


def _smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _zone_value(s: float, s0: float, s1: float, value: float, ramp: float = 0.4) -> float:
    if s < s0 - 1e-9 or s > s1 + 1e-9:
        return 0.0
    return value * _smooth((s - s0) / ramp) * (1 - _smooth((s - (s1 - ramp)) / ramp))


def compile_course(src, cal: dict | None = None) -> Compiled:
    course = Course.load(src)
    cal = cal or calibration.load()
    raw = course.raw
    w = raw.get("walker", {})
    speed = float(w.get("speed_mps", 0.8))
    circ = float(w.get("wheel_circ_m", 0.64))
    track = float(w.get("track_m", 0.5))
    k = float(raw.get("rig", {}).get("time_scale", 2))
    v = speed / k                                  # real metres per second of rig time
    route = course.route
    errors: list[str] = []
    warnings: list[str] = []
    if not cal.get("measured"):
        warnings.append("wheel servos not calibrated yet: using a typical FS90R curve "
                        "(run: python rig_driver.py calibrate)")

    # ---- objects converted to positions on the rounded route
    point_events, zones, truth_objects = [], [], []
    for o in raw.get("objects", []):
        t = o["type"]
        if t in ("crack", "curb"):
            rs = course.route_s(float(o["s"]))
            point_events.append({**o, "route_s": rs})
            x, y = course.path.point_at(float(o["s"]))
            truth_objects.append({**o, "route_s": round(rs, 3), "x": round(x, 3), "y": round(y, 3)})
        elif t in ("slope", "cross", "rough"):
            r0, r1 = sorted((course.route_s(float(o["s0"])), course.route_s(float(o["s1"]))))
            zones.append({**o, "r0": r0, "r1": r1})
            truth_objects.append({**o, "route_s0": round(r0, 3), "route_s1": round(r1, 3)})
        else:
            warnings.append(f"unknown object type '{t}' ignored")
    point_events.sort(key=lambda e: e["route_s"])

    deck = deck_settings.load()
    yaw_sign = deck["yaw_sign"]
    can_lo, can_hi = deck_settings.heading_range(deck)      # what the yaw servo can show
    need_lo, need_hi = course.heading_range_deg()
    # Only heading *changes* matter to the IMU, so if the turns don't fit one side of the (trimmed)
    # yaw servo, start the deck turned the other way: yaw_start is added to every heading.
    yaw_start = 0.0
    if need_hi > can_hi:
        yaw_start = can_hi - need_hi
    elif need_lo < can_lo:
        yaw_start = can_lo - need_lo
    if need_hi - need_lo > can_hi - can_lo + 0.5:
        errors.append(f"the path turns through {need_hi - need_lo:.0f} degrees in total ({need_hi:.0f} left "
                      f"and {-need_lo:.0f} right of its first leg); the yaw servo covers "
                      f"{can_hi - can_lo:.0f}. Keep the legs within that")
    elif abs(yaw_start) > 0.5:
        warnings.append(f"the deck starts turned {abs(yaw_start):.0f} degrees {'left' if yaw_start > 0 else 'right'} "
                        f"so the turns fit the trimmed yaw servo (headings are measured from the start)")

    def tilt(s: float, kind: str, key: str) -> float:
        deg = 0.0
        for z in zones:
            if z["type"] == kind:
                deg += math.degrees(math.atan(_zone_value(s, z["r0"], z["r1"], float(z.get(key, 0)) / 100)))
        return deg

    def buzz(s: float) -> int:
        for z in zones:
            if z["type"] == "rough" and z["r0"] <= s <= z["r1"]:
                return BUZZ.get(z.get("level", "light"), 1)
        return 0

    def yaw_servo(heading_deg: float) -> int:
        # untrimmed servo angle (the Arduino adds the trim); clipped so trimmed stays in 0..180
        heading_deg += yaw_start
        clipped = max(can_lo, min(can_hi, heading_deg))
        if abs(clipped - heading_deg) > 0.5:
            warnings.append("yaw clipped where the path turns further than the trimmed yaw servo reaches")
        return int(round(SERVO_CENTRE + yaw_sign * clipped))

    def servo(deg: float, label: str, limit: float = TILT_LIMIT) -> int:
        if abs(deg) > limit + 1e-9:
            warnings.append(f"{label} of {deg:.1f} degrees clipped to ±{limit}")
            deg = max(-limit, min(limit, deg))
        return int(round(SERVO_CENTRE + deg))

    # ---- walk the timeline
    lines: list[Line] = []
    times: dict[str, float] = {}
    t = 0.0
    s = 0.0
    yaw = 0.0                                      # heading change from the start, radians CCW
    next_event = 0
    curb_left = 0.0
    peak_rps = 0.0
    low_rps = math.inf                             # slowest moving wheel (the inner one in corners)
    top_rps = calibration.max_rps(cal)
    smooth_rps = calibration.min_smooth_rps(cal)

    def smooth_heading(at: float, half: float = 0.1) -> float:
        a = route.point_at(max(0.0, at - half))
        b = route.point_at(min(route.length, at + half))
        return math.atan2(b[1] - a[1], b[0] - a[0])

    prev_h = smooth_heading(0.0)

    def emit(l_rps: float, r_rps: float, pitch_extra: float = 0.0, jolt: int = JOLT_NONE) -> None:
        nonlocal peak_rps, low_rps
        peak_rps = max(peak_rps, l_rps, r_rps)
        if l_rps > 0.005 and r_rps > 0.005:
            low_rps = min(low_rps, l_rps, r_rps)
        lines.append(Line(
            t_ms=int(round(t * 1000)),
            left_us=calibration.pulse_for(cal["left"], l_rps),
            right_us=calibration.pulse_for(cal["right"], r_rps),
            yaw_deg=yaw_servo(math.degrees(yaw)),
            pitch_deg=servo(tilt(s, "slope", "grade_pct") + pitch_extra, "pitch"),
            roll_deg=servo(tilt(s, "cross", "tilt_pct"), "roll"),
            jolt=jolt, buzz=buzz(s) if curb_left <= 0 else 0, s=round(s, 3)))

    while t < MOVE_AT_S - 1e-9:                    # standstill + sync jolt
        emit(0, 0, jolt=JOLT_SYNC if abs(t - SYNC_AT_S) < TICK_S / 2 else JOLT_NONE)
        t += TICK_S
    times["move"] = t

    while s < route.length - 1e-9:
        if next_event < len(point_events) and point_events[next_event]["type"] == "curb" \
                and s >= point_events[next_event]["route_s"]:
            ev = point_events[next_event]
            next_event += 1
            times[ev.get("id", f"curb@{ev['route_s']:.1f}")] = t
            curb_left = CURB_PAUSE_S
            while curb_left > 1e-9:                # stop, nod up (the lift), jolt on the drop
                el = CURB_PAUSE_S - curb_left
                nod = CURB_NOD_DEG * _smooth(el / 0.35) * (1 - _smooth((el - 0.35) / 0.15))
                emit(0, 0, pitch_extra=nod, jolt=JOLT_CURB if abs(el - 0.5) < TICK_S / 2 else JOLT_NONE)
                t += TICK_S
                curb_left -= TICK_S
            continue

        ds = min(v * TICK_S, route.length - s)
        h = smooth_heading(s + ds)
        dh = math.atan2(math.sin(h - prev_h), math.cos(h - prev_h))
        prev_h = h
        yaw += dh
        omega = dh / TICK_S
        l_rps = (v - omega * track / 2) / circ
        r_rps = (v + omega * track / 2) / circ

        jolt = JOLT_NONE
        while next_event < len(point_events) and point_events[next_event]["type"] == "crack" \
                and s + ds >= point_events[next_event]["route_s"]:
            ev = point_events[next_event]
            next_event += 1
            jolt = JOLT_LARGE if ev.get("severity") == "large" else JOLT_SMALL
            times[ev.get("id", f"crack@{ev['route_s']:.1f}")] = t
        s += ds
        emit(l_rps, r_rps, jolt=jolt)
        t += TICK_S

    emit(0, 0)
    if peak_rps > top_rps:
        errors.append(f"wheels would need {peak_rps:.2f} turns/s but the servos top out near {top_rps:.2f}; "
                      f"raise rig.time_scale above {k * peak_rps / top_rps:.1f} or widen the corners")
    if low_rps < smooth_rps - 0.01:
        warnings.append(f"the inner wheel needs {low_rps:.2f} turns/s in corners, below the slowest the "
                        f"servos run smoothly ({smooth_rps:.2f}), so it will stutter or stop there; "
                        f"widen the corners (corner_radius_m)")

    for o in truth_objects:
        if o.get("id") in times:
            o["rig_t_s"] = round(times[o["id"]], 3)
    truth = {
        "course": raw,
        "route_length_m": round(route.length, 3),
        "time_scale": k,
        "tick_s": TICK_S,
        "sync_jolt_s": SYNC_AT_S,
        "move_s": MOVE_AT_S,
        "yaw_start_deg": round(yaw_start, 1),       # deck's starting turn (left positive), see above
        "yaw_sign": yaw_sign,                       # yaw servo = 90 + yaw_sign * (heading + yaw_start)
        "has_roll_servo": bool(deck.get("has_roll_servo")),   # without it cross-slopes can't be felt
        "wheel_calibrated": bool(cal.get("measured")),
        "peak_wheel_rps": round(peak_rps, 3),
        "objects": truth_objects,
    }
    return Compiled(lines, truth, t, errors, sorted(set(warnings)))


def timeline_rows(c: Compiled) -> list[dict]:
    return [asdict(x) for x in c.lines]
