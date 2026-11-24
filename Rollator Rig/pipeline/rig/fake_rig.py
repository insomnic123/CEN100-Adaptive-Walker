"""Software stand-ins for the Arduino rig and the ESP32 wheel counter (same serial protocols).

    python rig_driver.py run ../data/courses/example.json --fake --speedup 20
    python rig_driver.py calibrate --fake
"""
from __future__ import annotations

import time
from collections import deque

from .calibration import TYPICAL


class FakeRig:
    QUEUE = 24

    def __init__(self, speedup: float = 1.0):
        self.speedup = speedup
        self.out: deque[bytes] = deque()
        self.buf = b""
        self.wheels_us = (0, 0)                     # for FakeWheelCounter during calibration
        self._reset()
        self.out.append(f"READY,{self.QUEUE}\n".encode())

    # ---- pyserial-like interface
    def write(self, data: bytes) -> int:
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            self._handle(line.decode().strip())
        return len(data)

    def readline(self) -> bytes:
        self._step()
        if not self.out:
            time.sleep(0.005)
            self._step()
        return self.out.popleft() if self.out else b""

    def close(self) -> None:
        pass

    # ---- emulated firmware
    def _reset(self) -> None:
        self.queue: deque[list[int]] = deque()
        self.running = self.ending = False
        self.t0 = time.monotonic()
        self.last = 0.0
        self.next_report = 0
        self.executed = 0

    def _now_ms(self) -> float:
        return (time.monotonic() - self.t0) * 1000 * self.speedup

    def _handle(self, line: str) -> None:
        if not line:
            return
        cmd = line[0]
        if cmd == "H":
            self._reset()
            self.out.append(f"READY,{self.QUEUE}\n".encode())
        elif cmd == "T":
            if len(self.queue) >= self.QUEUE:
                self.out.append(b"ERR,queue overflow\n")
                return
            self.queue.append([int(x) for x in line.split(",")[1:]])
        elif cmd == "B":                             # start the clock; keep the prefilled queue
            self.t0, self.last, self.next_report, self.executed = time.monotonic(), 0.0, 0, 0
            self.running = True
        elif cmd == "E":
            self.ending = True
        elif cmd == "X":
            self._reset()
            self.out.append(b"DONE,aborted\n")
        elif cmd == "D":                             # deck trims: only matter on the real servos
            self.out.append(b"OK\n")
        elif cmd == "C":
            _, l, r = line.split(",")
            self.wheels_us = (int(l), int(r))
            self.out.append(b"OK\n")

    def _step(self) -> None:
        if not self.running:
            return
        now = self._now_ms()
        while self.last < now:
            t = min(now, self.last + 5)
            while self.queue and self.queue[0][0] <= t:
                self.queue.popleft()
                self.executed += 1
            self.last = t
            if t >= self.next_report:
                self.out.append(f"P,{int(t)},{self.executed},{len(self.queue)}\n".encode())
                self.next_report += 50
            if self.ending and not self.queue:
                self._reset()
                self.out.append(b"DONE\n")
                return


class FakeWheelCounter:
    """Pretends to be the ESP32 counting wheel holes (photoresistors), with a slightly 'off'
    servo curve, so calibration has something to find."""

    HOLES = 4

    def __init__(self, rig: FakeRig, speedup: float = 1.0):
        self.rig = rig
        self.speedup = speedup
        self.t0 = time.monotonic()
        self.last = self.t0
        self.counts = [0.0, 0.0]
        self.edges = [0, 0]                         # ms of each wheel's latest hole
        self.next = 0.0

    @staticmethod
    def _rps(us: int, scale: float) -> float:
        if us == 0:
            return 0.0
        off = abs(us - 1500) - 8                    # this fake servo's neutral is 8 us off
        if off <= 0:
            return 0.0
        for (u0, r0), (u1, r1) in zip(TYPICAL, TYPICAL[1:]):
            if u0 <= off <= u1:
                return scale * (r0 + (r1 - r0) * (off - u0) / (u1 - u0))
        return scale * TYPICAL[-1][1]

    def readline(self) -> bytes:
        now = time.monotonic()
        dt = (now - self.last) * self.speedup
        self.last = now
        l_us, r_us = self.rig.wheels_us
        ms = (now - self.t0) * 1000 * self.speedup
        for i, (us, scale) in enumerate(((l_us, 1.06), (r_us, 0.95))):
            before = int(self.counts[i])
            self.counts[i] += self._rps(us, scale) * self.HOLES * dt
            if int(self.counts[i]) > before:
                self.edges[i] = int(ms)
        if ms < self.next:
            time.sleep(0.01)
            return b""
        self.next = ms + 100
        return (f"H,{int(ms)},{int(self.counts[0])},{int(self.counts[1])},"
                f"{self.edges[0]},{self.edges[1]}\n").encode()

    def close(self) -> None:
        pass
