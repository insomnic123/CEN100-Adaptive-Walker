"""Measures each wheel servo's speed curve using the ESP32's Hall sensors.

The Arduino spins one wheel at a time (C command); the ESP32 running
firmware/hall_counter_esp32 reports magnet counts. Result: data/calibration.json.
Hall sensors can't tell direction, so check that first with `rig_driver.py spin`
and pass --invert-left / --invert-right if a wheel turns backwards.
"""
from __future__ import annotations

import time

from . import calibration
from .driver import RigError, handshake

MAGNETS = 8
OFFSETS = [20, 40, 60, 80, 100, 150, 200, 300, 400]   # us from neutral
NEUTRAL_SEARCH = range(-40, 41, 5)


def _counts(esp):
    raw = esp.readline()
    if not raw:
        return None
    parts = raw.decode(errors="replace").strip().split(",")
    if len(parts) == 4 and parts[0] == "H":
        try:
            return int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            return None
    return None


def _wait_until(esp, ms_target: float, timeout_s: float = 30):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        c = _counts(esp)
        if c and c[0] >= ms_target:
            return c
    raise RigError("no data from the ESP32: is hall_counter_esp32 uploaded and the port right?")


def _now(esp):
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


def _measure(rig, esp, side: str, us: int, settle_s: float, window_s: float) -> float:
    _drive(rig, us if side == "left" else 0, us if side == "right" else 0)
    start = _now(esp)
    a = _wait_until(esp, start[0] + settle_s * 1000)
    b = _wait_until(esp, a[0] + window_s * 1000)
    i = 1 if side == "left" else 2
    return (b[i] - a[i]) / MAGNETS / ((b[0] - a[0]) / 1000)


def run(rig, esp, invert: dict[str, bool], log=print) -> dict:
    handshake(rig)
    cal = calibration.default()
    try:
        for side in ("left", "right"):
            sign = -1 if invert.get(side) else 1
            log(f"{side} wheel: finding the stop point")
            still = [d for d in NEUTRAL_SEARCH if _measure(rig, esp, side, 1500 + d, 0.6, 1.5) < 0.02]
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
                rps = _measure(rig, esp, side, neutral + sign * off, 0.8, 3.0)
                rps = max(rps, table[-1][1])          # keep the curve monotonic
                table.append([off, round(rps, 3)])
                log(f"  +{off:>3} us -> {rps:.2f} turns/s")
            cal[side] = {"neutral_us": neutral, "invert": bool(invert.get(side)), "table": table}
    finally:
        try:
            _drive(rig, 0, 0)
        except RigError:
            pass
    cal["measured"] = True
    return cal
