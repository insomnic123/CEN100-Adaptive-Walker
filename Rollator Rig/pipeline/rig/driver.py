"""Streams a compiled timeline to the Arduino with flow control, and records what happened."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Callable

from .compiler import Compiled
from .protocol import BAUD, parse


class RigError(RuntimeError):
    pass


def open_port(port: str | None, fake: bool = False, speedup: float = 1.0):
    if fake:
        from .fake_rig import FakeRig
        return FakeRig(speedup=speedup)
    import serial                                    # pyserial
    if not port:
        raise RigError("no serial port given (use --port, or --fake to test without hardware)")
    return serial.Serial(port, BAUD, timeout=0.02)


def _readline(link) -> str:
    raw = link.readline()
    return raw.decode(errors="replace").strip() if raw else ""


def handshake(link, wait_s: float = 6.0) -> int:
    """Opening the port resets the Arduino (about 2 s). Wait for READY, nudging with H."""
    deadline = time.monotonic() + wait_s
    next_nudge = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        tag, val = parse(_readline(link))
        if tag == "READY":
            return int(val)
        if time.monotonic() >= next_nudge:
            link.write(b"H\n")
            next_nudge = time.monotonic() + 1.0
    raise RigError("the Arduino never said READY: check the port, the baud rate (115200), "
                   "and that the Serial Monitor is closed")


def stream(link, compiled: Compiled, run_dir: Path,
           on_progress: Callable[[float], None] | None = None,
           should_abort: Callable[[], bool] | None = None) -> dict:
    """Send the timeline, keep the Arduino's queue topped up, log every progress report."""
    if compiled.errors:
        raise RigError("course has errors:\n  " + "\n  ".join(compiled.errors))
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "truth.json").write_text(json.dumps(compiled.truth, indent=2))
    with open(run_dir / "timeline.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["t_ms", "left_us", "right_us", "yaw_deg", "pitch_deg", "roll_deg", "jolt", "buzz", "s"])
        for ln in compiled.lines:
            wr.writerow([ln.t_ms, ln.left_us, ln.right_us, ln.yaw_deg, ln.pitch_deg, ln.roll_deg,
                         ln.jolt, ln.buzz, ln.s])

    queue_size = handshake(link)
    window = max(4, queue_size - 4)                  # keep a little headroom on the Arduino
    lines = compiled.lines
    n = len(lines)
    sent = executed = 0

    log = open(run_dir / "rig_log.csv", "w", newline="")
    logw = csv.writer(log)
    logw.writerow(["t_ms", "executed", "queued", "s"])

    def top_up() -> None:
        nonlocal sent
        while sent < n and sent - executed < window:
            link.write((lines[sent].wire() + "\n").encode())
            sent += 1

    top_up()                                         # prefill before the clock starts
    link.write(b"B\n")
    ended = aborted = False
    last_report = time.monotonic()
    last_ui = 0.0
    try:
        while True:
            tag, val = parse(_readline(link))
            now = time.monotonic()
            if tag == "P":
                executed = val.executed
                last_report = now
                s = lines[min(executed, n) - 1].s if executed else 0.0
                logw.writerow([val.t_ms, val.executed, val.queued, s])
                if on_progress and now - last_ui > 0.2:
                    on_progress(s)
                    last_ui = now
            elif tag == "DONE":
                aborted = bool(val)
                break
            elif tag == "ERR":
                print(f"  rig: ERR {val}")
            elif val and tag is None:
                print(f"  rig: {val}")

            if not aborted and should_abort and should_abort():
                link.write(b"X\n")
                aborted = True
            if aborted:
                if now - last_report > 3.0:
                    raise RigError("aborted, but the Arduino never confirmed DONE")
                continue
            top_up()
            if sent >= n and not ended:
                link.write(b"E\n")
                ended = True
            if now - last_report > 3.0:
                raise RigError("no progress reports from the Arduino for 3 s")
    finally:
        log.close()
    if on_progress and not aborted:
        on_progress(lines[-1].s)
    return {"aborted": aborted, "lines": n, "duration_s": compiled.duration_s}
