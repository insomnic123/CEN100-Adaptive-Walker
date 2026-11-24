"""Serial protocol between rig_driver.py and the Arduino (115200 baud, ASCII lines).

Laptop -> Arduino
  H                                  reset: stop everything, clear the queue; answers READY
  T,t,left_us,right_us,yaw,pitch,roll,jolt,buzz
                                     one timeline line (see compiler.Line), queued
  B                                  begin: the Arduino starts its run clock at t = 0
  E                                  end of stream: finish the queue, centre the deck, DONE
  X                                  abort now: stop wheels, centre the deck, DONE,aborted
  C,left_us,right_us                 calibration only: drive the wheels directly (0 = stop); answers OK
  D,yaw_trim,pitch_trim,roll_trim    deck trims in degrees (data/deck.json), idle only; re-centres the
                                     deck and answers OK. The driver sends it after every READY.

Arduino -> laptop
  READY,<queue_size>                 after boot and after H
  P,t,executed,queued                progress, every 50 ms while running
  DONE[,aborted]                     finished; wheels stopped and the deck centred
  OK                                 reply to C and D
  ERR,<message>                      bad line, queue overflow, or starved (queue ran dry)

ESP32 photoresistor wheel counter (calibration only, its own USB port; 4 holes per wheel)
  H,ms,left_count,right_count,left_edge_ms,right_edge_ms
                                     every 100 ms; *_edge_ms = ESP32 time of that wheel's latest hole
  L,left_level,right_level,left_threshold,right_threshold
                                     optional (SHOW_LEVELS), light readings for setting up; ignored
"""
from __future__ import annotations

from dataclasses import dataclass

BAUD = 115200


@dataclass
class Progress:
    t_ms: int
    executed: int
    queued: int


def parse(line: str):
    """('READY', n) | ('P', Progress) | ('DONE', aborted) | ('OK', None) | ('ERR', msg) | (None, line)."""
    parts = line.strip().split(",")
    tag = parts[0]
    try:
        if tag == "READY":
            return "READY", int(parts[1]) if len(parts) > 1 else 24
        if tag == "P" and len(parts) == 4:
            return "P", Progress(*map(int, parts[1:]))
        if tag == "DONE":
            return "DONE", len(parts) > 1 and parts[1] == "aborted"
        if tag == "OK":
            return "OK", None
        if tag == "ERR":
            return "ERR", ",".join(parts[1:])
    except ValueError:
        pass
    return None, line.strip()
