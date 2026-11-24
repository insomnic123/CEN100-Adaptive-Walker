"""Records the ESP32 sensor kit (firmware/sensor_esp32) over USB while a run plays.

Files written into the run's folder:
  imu.csv          host_t, esp_ms, ax, ay, az, gx, gy, gz   (raw: 4096 = 1 g, 32.8 = 1 deg/s)
  wheels.csv       host_t, esp_ms, left, right, left_edge_ms, right_edge_ms   (notch counts)
  button.csv       host_t, esp_ms, down
  sensor_meta.json host_begin (laptop clock at the run's t = 0), imu id, line counts, problems

host_t is the laptop's time.monotonic() when the line arrived; together with host_begin it lines
the sensor log up with the run clock to within a few ms (process.py then refines it with the
sync jolt).

FakeSensor writes the same files from a run's timeline.csv, imitating the servos and an IMU
mounted at an odd angle, so the pipeline can be tested without hardware.
"""
from __future__ import annotations

import csv
import json
import math
import random
import threading
import time
from pathlib import Path

ESP_BAUD = 921600
ACC_LSB = 4096.0              # per g   (+-8 g)
GYRO_LSB = 32.8               # per deg/s (+-1000 deg/s)


class SensorRecorder:
    def __init__(self, port: str, run_dir: Path):
        import serial
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.ser = serial.Serial()
        self.ser.port, self.ser.baudrate, self.ser.timeout = port, ESP_BAUD, 0.1
        self.ser.dtr = False                       # don't reset the ESP32 by opening the port
        self.ser.rts = False
        try:
            self.ser.open()
        except serial.SerialException as e:
            from .driver import RigError
            raise RigError(f"could not open the ESP32 port {port}: {e}. Close any Serial Monitor on it.") from None
        self.host_begin: float | None = None
        self.counts = {"I": 0, "W": 0, "K": 0, "bad": 0}
        self.imu_id = None
        self.problems: list[str] = []
        self._stop = threading.Event()
        self._files = {k: open(self.run_dir / f"{name}.csv", "w", newline="")
                       for k, name in (("I", "imu"), ("W", "wheels"), ("K", "button"))}
        self._w = {k: csv.writer(f) for k, f in self._files.items()}
        self._w["I"].writerow(["host_t", "esp_ms", "ax", "ay", "az", "gx", "gy", "gz"])
        self._w["W"].writerow(["host_t", "esp_ms", "left", "right", "left_edge_ms", "right_edge_ms"])
        self._w["K"].writerow(["host_t", "esp_ms", "down"])
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def mark_begin(self, host_t: float) -> None:
        self.host_begin = host_t

    def _loop(self) -> None:
        self.ser.reset_input_buffer()
        while not self._stop.is_set():
            raw = self.ser.readline()
            if not raw:
                continue
            host_t = time.monotonic()
            parts = raw.decode(errors="replace").strip().split(",")
            tag = parts[0]
            try:
                if tag == "I" and len(parts) == 8:
                    self._w["I"].writerow([f"{host_t:.4f}", *(int(p) for p in parts[1:])])
                elif tag == "W" and len(parts) == 6:
                    self._w["W"].writerow([f"{host_t:.4f}", *(int(p) for p in parts[1:])])
                elif tag == "K" and len(parts) == 3:
                    self._w["K"].writerow([f"{host_t:.4f}", int(parts[1]), int(parts[2])])
                elif tag == "S" and len(parts) >= 3:
                    self.imu_id = parts[2]
                    continue
                elif tag == "E":
                    msg = ",".join(parts[1:])
                    if msg not in self.problems:
                        self.problems.append(msg)
                    continue
                else:
                    self.counts["bad"] += 1            # boot messages, partial lines
                    continue
                self.counts[tag] += 1
            except ValueError:
                self.counts["bad"] += 1

    def imu_lines(self) -> int:
        return self.counts["I"]

    def stop(self) -> dict:
        self._stop.set()
        self._thread.join(timeout=2)
        self.ser.close()
        for f in self._files.values():
            f.close()
        meta = {"host_begin": self.host_begin, "imu_id": self.imu_id, "lines": self.counts,
                "problems": self.problems, "fake": False}
        (self.run_dir / "sensor_meta.json").write_text(json.dumps(meta, indent=2))
        return meta


# ---------------------------------------------------------------- fake sensor (for testing)
JOLT_DEG = {1: 6, 2: 12, 3: 18, 4: 10}
BUZZ_DEG = {1: 3, 2: 6}


def _rot(axis: tuple[float, float, float], ang: float):
    x, y, z = axis
    c, s, C = math.cos(ang), math.sin(ang), 1 - math.cos(ang)
    return [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mv(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def _T(m):
    return [[m[j][i] for j in range(3)] for i in range(3)]


def fake_sensor(run_dir: Path, seed: int = 1) -> dict:
    """Writes imu.csv / wheels.csv / sensor_meta.json for a finished (fake) run from its timeline."""
    run_dir = Path(run_dir)
    rnd = random.Random(seed)
    truth = json.loads((run_dir / "truth.json").read_text())
    circ = float(truth["course"].get("walker", {}).get("wheel_circ_m", 0.64))
    track = float(truth["course"].get("walker", {}).get("track_m", 0.5))
    rows = list(csv.DictReader(open(run_dir / "timeline.csv")))
    # deck command at 1 ms steps, imitating the firmware's jolt / buzz flicks
    end_ms = int(rows[-1]["t_ms"]) + 1500
    pitch_cmd = [0.0] * end_ms
    yaw_cmd = [0.0] * end_ms
    s_at = [0.0] * end_ms
    j = 0
    jolt_until, jolt_amp, buzz, buzz_next, buzz_up = -1, 0, 0, 0, False
    for t in range(end_ms):
        while j < len(rows) and int(rows[j]["t_ms"]) <= t:
            r = rows[j]
            base_p, base_y, s_now = float(r["pitch_deg"]) - 90, float(r["yaw_deg"]) - 90, float(r["s"])
            buzz = int(r["buzz"])
            if int(r["jolt"]):
                jolt_amp, jolt_until = JOLT_DEG[int(r["jolt"])], t + 70
            j += 1
        off = 0.0
        if t < jolt_until:
            off = jolt_amp
        elif buzz:
            if t >= buzz_next:
                buzz_up, buzz_next = not buzz_up, t + 80
            off = BUZZ_DEG[buzz] if buzz_up else 0.0
        pitch_cmd[t], yaw_cmd[t], s_at[t] = base_p + off, base_y, s_now
    # servo response: first-order lag + speed limit (SG90 ~ 600 deg/s)
    def servo(cmd, tau=0.025, rate=600.0):
        out, x = [], cmd[0]
        for c in cmd:
            dx = (c - x) * (0.001 / tau)
            dx = max(-rate * 0.001, min(rate * 0.001, dx))
            x += dx
            out.append(x)
        return out
    pitch, yaw = servo(pitch_cmd), servo(yaw_cmd, tau=0.05, rate=400.0)
    # IMU mounted at an odd angle on the deck (like the real one: ~9 deg tipped, axes turned)
    mount = _mul(_rot((0, 0, 1), math.radians(rnd.uniform(0, 360))), _rot((0, 1, 0), math.radians(-8.8)))
    mount = _mul(mount, _rot((1, 0, 0), math.radians(rnd.uniform(-3, 3))))
    mT = _T(mount)
    esp0 = rnd.randint(20000, 90000)               # ESP32 clock at the run's t = 0
    host_begin = 1000.0
    lag = 0.004
    imu_f = open(run_dir / "imu.csv", "w", newline="")
    wh_f = open(run_dir / "wheels.csv", "w", newline="")
    iw, ww = csv.writer(imu_f), csv.writer(wh_f)
    iw.writerow(["host_t", "esp_ms", "ax", "ay", "az", "gx", "gy", "gz"])
    ww.writerow(["host_t", "esp_ms", "left", "right", "left_edge_ms", "right_edge_ms"])
    edges = [0, 0]
    last_counts = [0, 0]
    start = -2000                                   # sensor log starts 2 s before the run
    for t in range(start, end_ms - 1, 5):
        k = max(0, t)
        p, y = math.radians(pitch[k]), math.radians(yaw[k])
        dp = math.radians(pitch[min(k + 1, end_ms - 1)] - pitch[k]) * 1000
        dy = math.radians(yaw[min(k + 1, end_ms - 1)] - yaw[k]) * 1000
        # deck = yaw about world z, then pitch about deck y; IMU = deck * mount
        R_deck = _mul(_rot((0, 0, 1), y), _rot((0, 1, 0), -p))
        g_deck = _mv(_T(R_deck), [0, 0, 1])          # gravity (specific force) in deck frame
        w_deck = [a + b for a, b in zip(_mv(_rot((0, 1, 0), p), [0, 0, dy]), [0, -dp, 0])]
        g = _mv(mT, g_deck)
        w = _mv(mT, w_deck)
        acc = [int(round((gi + rnd.gauss(0, 0.004)) * ACC_LSB)) for gi in g]
        gyr = [int(round((math.degrees(wi) + 0.6 + rnd.gauss(0, 0.15)) * GYRO_LSB)) for wi in w]
        esp = esp0 + t
        host = host_begin + t / 1000 + lag + abs(rnd.gauss(0, 0.002))
        iw.writerow([f"{host:.4f}", esp, *acc, *gyr])
        if t % 100 == 0:
            s = s_at[k]
            yaw_rad = math.radians(-(yaw_cmd[k] - yaw_cmd[0]))
            turns = [(s - yaw_rad * track / 2) / circ, (s + yaw_rad * track / 2) / circ]
            counts = [max(0, int(tn * 4)) for tn in turns]
            for i in range(2):
                if counts[i] != last_counts[i]:
                    edges[i] = esp
                    last_counts[i] = counts[i]
            ww.writerow([f"{host:.4f}", esp, *counts, *edges])
    imu_f.close()
    wh_f.close()
    meta = {"host_begin": host_begin, "imu_id": "70", "lines": {}, "problems": [], "fake": True}
    (run_dir / "sensor_meta.json").write_text(json.dumps(meta, indent=2))
    return meta
