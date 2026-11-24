"""Deck servo settings, shared by the driver, the compiler and the website: data/deck.json.

  yaw_trim, pitch_trim, roll_trim   degrees added to every deck servo angle by the Arduino, so the
                                    deck rests straight and level at "centre". Set them live with
                                    `python rig_driver.py trim --port COMx`.
  yaw_sign                          -1 or 1: flip if the deck turns the wrong way in corners.

A servo only reaches 0-180 degrees, so a yaw trim takes turning range from one side:
with yaw_trim = 30 the deck can still turn 120 degrees one way but only 60 the other.
"""
from __future__ import annotations

import json

from .db import ROOT

DECK_PATH = ROOT / "data" / "deck.json"
DEFAULT = {"yaw_trim": 0, "pitch_trim": 0, "roll_trim": 0, "yaw_sign": -1}
TRIM_LIMIT = 60               # degrees either way; beyond this, re-seat the servo horn


def load() -> dict:
    d = dict(DEFAULT)
    try:
        d.update({k: int(v) for k, v in json.loads(DECK_PATH.read_text()).items() if k in DEFAULT})
    except (OSError, ValueError):
        pass
    d["yaw_sign"] = -1 if d["yaw_sign"] < 0 else 1
    return d


def save(d: dict) -> None:
    DECK_PATH.parent.mkdir(parents=True, exist_ok=True)
    DECK_PATH.write_text(json.dumps({k: int(d[k]) for k in DEFAULT}, indent=2) + "\n")


def wire(d: dict) -> str:
    """The serial line that sets the trims on the Arduino."""
    return f"D,{d['yaw_trim']},{d['pitch_trim']},{d['roll_trim']}"


def heading_range(d: dict) -> tuple[float, float]:
    """(lowest, highest) heading change in degrees (CCW = left = positive) the yaw servo can show.

    The servo gets 90 + yaw_sign * heading + yaw_trim, which must stay within 0..180.
    """
    lo, hi = -90.0 - d["yaw_trim"], 90.0 - d["yaw_trim"]     # allowed for yaw_sign * heading
    return (lo, hi) if d["yaw_sign"] > 0 else (-hi, -lo)
