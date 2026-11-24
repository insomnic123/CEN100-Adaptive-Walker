"""Measures each wheel servo's speed curve with the ESP32's photoresistor wheel counters.

Each wheel has 4 holes; an LED shines through them onto a photoresistor. The Arduino spins
one wheel at a time (C command); the ESP32 running firmware/photo_counter_esp32 reports
hole counts plus the time of the latest hole. Result: data/calibration.json.
A light beam can't tell direction, so check that first with `rig_driver.py spin`
and pass --invert-left / --invert-right if a wheel turns backwards.

Speed comes from the time between holes (not a count over a fixed window), so slow speeds
still measure well with only 4 holes per turn.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import calibration
from .driver import RigError, handshake

HOLES = 4                     # holes per wheel = counts per turn
OFFSETS = [20, 40, 60, 80, 100, 150, 200, 300, 400]   # us from neutral
NEUTRAL_SEARCH = range(-40, 41, 5)

STILL_WINDOW_S = 3.0          # stop-point search: no hole in 3 s counts as stopped
MIN_COUNTS = 8                # speed: time at least 2 full turns...
MAX_WINDOW_S = 8.0            # ...but give up after 8 s (very slow speeds)


@dataclass
class Reading:
    ms: int                   # ESP32 clock
    count: tuple[int, int]    # (left, right) holes so far
    edge: tuple[int, int]     # (left, right) ESP32 time of the latest hole


def _reading(esp) -> Reading | None:
    raw = esp.readline()
    if not raw:
        return None
    parts = raw.decode(errors="replace").strip().split(",")
    if parts[0] != "H" or len(parts) not in (4, 6):
        return None                                   # also skips the optional L,... level lines
    try:
        v = [int(p) for p in parts[1:]]
    except ValueError:
        return None
    ms, left, right = v[0], v[1], v[2]
    edge = (v[3], v[4]) if len(v) == 5 else (ms, ms)  # old 4-field lines: line time only
    return Reading(ms, (left, right), edge)


def _wait_until(esp, ms_target: float, timeout_s: float = 30) -> Reading:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = _reading(esp)
        if r and r.ms >= ms_target:
            return r
    raise RigError("no data from the ESP32: is photo_counter_esp32 uploaded and the port right?")


def _now(esp) -> Reading:
    return _wait_until(esp, 0)


def _drive(rig, left_us: int, right_us: int) -> None:
    rig.write(f"C,{left_us},{right_us}\n".encode())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        raw = rig.readline()
        if raw and raw.strip() == b"OK":
            return
        if raw and raw.startswith(b"ERR"):
            raise RigError(raw.decode().strip())
    raise RigError("the Arduino did not answer the calibration command")


def _measure(rig, esp, side: str, us: int, settle_s: float,
             min_counts: int, max_window_s: float) -> float:
    """Turns/s of one wheel at one pulse width, from the time between its holes."""
    _drive(rig, us if side == "left" else 0, us if side == "right" else 0)
    i = 0 if side == "left" else 1
    start = _now(esp)
    a = _wait_until(esp, start.ms + settle_s * 1000)
    first: tuple[int, int] | None = None              # (count, edge ms) at the first hole seen
    last: tuple[int, int] | None = None
    while True:
        r = _wait_until(esp, 0)
        if r.count[i] > a.count[i]:
            if first is None:
                first = (r.count[i], r.edge[i])
            last = (r.count[i], r.edge[i])
        if first and last[0] - first[0] >= min_counts:
            break
        if r.ms - a.ms >= max_window_s * 1000:
            break
    if first and last and last[0] > first[0] and last[1] > first[1]:
        return (last[0] - first[0]) / HOLES / ((last[1] - first[1]) / 1000)
    holes = (last[0] - a.count[i]) if last else 0     # 0 or 1 hole: only a rough upper bound
    return holes / HOLES / max_window_s


def run(rig, esp, invert: dict[str, bool], log=print) -> dict:
    handshake(rig)
    cal = calibration.default()
    try:
        for side in ("left", "right"):
            sign = -1 if invert.get(side) else 1
            log(f"{side} wheel: finding the stop point")
            still = [d for d in NEUTRAL_SEARCH
                     if _measure(rig, esp, side, 1500 + d, 0.6, 1, STILL_WINDOW_S) == 0]
            if still:
                # the middle of the dead band nearest 1500
                near = min(still, key=abs)
                band = [d for d in still if abs(d - near) <= 20]
                neutral = 1500 + round(sum(band) / len(band))
            else:
                neutral = 1500
                log(f"  {side}: never fully stopped near 1500 us; check the servo's trim screw")
            log(f"  neutral {neutral} us")
            table = [[0, 0.0]]
            for off in OFFSETS:
                rps = _measure(rig, esp, side, neutral + sign * off, 0.8, MIN_COUNTS, MAX_WINDOW_S)
                rps = max(rps, table[-1][1])          # keep the curve monotonic
                table.append([off, round(rps, 3)])
                log(f"  +{off:>3} us -> {rps:.2f} turns/s")
                if off >= 150 and rps < 0.05:
                    raise RigError(
                        f"the {side} wheel's photoresistor counted no holes even at +{off} us. Check the "
                        f"beam with firmware/part_tests/photo_test (turn the wheel by hand: 4 counts per "
                        f"turn), and that the {side} sensor is on GPIO {34 if side == 'left' else 35}. "
                        "Nothing was saved.")
            cal[side] = {"neutral_us": neutral, "invert": bool(invert.get(side)), "table": table}
    finally:
        try:
            _drive(rig, 0, 0)
        except RigError:
            pass
    cal["measured"] = True
    return cal
