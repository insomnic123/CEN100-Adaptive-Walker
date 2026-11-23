"""Wheel-servo calibration: which pulse width gives which wheel speed.

Continuous servos are told a speed, not a distance, and every unit is a little different.
`rig_driver.py calibrate` measures each wheel with the ESP32's Hall sensors and writes
data/calibration.json. Until then, a typical FS90R curve is used (expect a few % error).
"""
from __future__ import annotations

import json
from pathlib import Path

from .db import ROOT

CAL_PATH = ROOT / "data" / "calibration.json"

# typical FS90R at 5 V: (pulse offset from neutral in us, wheel turns per second)
TYPICAL = [[0, 0.0], [20, 0.05], [40, 0.25], [60, 0.45], [80, 0.62], [100, 0.75],
           [150, 0.98], [200, 1.12], [300, 1.27], [400, 1.33]]


def default() -> dict:
    side = {"neutral_us": 1500, "invert": False, "table": TYPICAL}
    return {"measured": False, "left": dict(side), "right": dict(side)}


def load(path: Path = CAL_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default()


def save(cal: dict, path: Path = CAL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cal, indent=2))


def max_rps(cal: dict) -> float:
    return min(cal["left"]["table"][-1][1], cal["right"]["table"][-1][1])


def pulse_for(side: dict, rps: float) -> int:
    """Pulse width (us) for a forward wheel speed in turns/s. 0 means stop (servo detached)."""
    if rps <= 0.005:
        return 0
    table = side["table"]
    if rps >= table[-1][1]:
        off = table[-1][0]
    else:
        off = table[-1][0]
        for (u0, r0), (u1, r1) in zip(table, table[1:]):
            if r0 <= rps <= r1:
                off = u0 + (u1 - u0) * ((rps - r0) / (r1 - r0) if r1 > r0 else 0)
                break
    sign = -1 if side.get("invert") else 1
    return int(round(side["neutral_us"] + sign * off))
