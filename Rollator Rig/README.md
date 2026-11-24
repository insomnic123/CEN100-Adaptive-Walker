# Rollator kit: rig sandbox

Draw a course in the browser, play it on the cardboard rig, and see what the sensor kit
detects. One repo, one Next.js site.

The rig is servos only, driven by an Arduino Uno R3 (a Mega works with the same pins):
two continuous servos spin the wheels, standard servos turn and tilt the IMU deck, and
crack/curb jolts are quick flicks of the pitch servo. The Arduino has no sensors: the
wheel servos are calibrated once with the ESP32's photoresistor wheel counters
(4 holes per wheel, an LED behind, a photoresistor in front).

```
rollator-rig/
├── README.md
├── data/                          shared by the site and Python
│   ├── schema.sql                 SQLite tables: courses, runs
│   ├── deck.json                  deck servo trims + yaw direction (set with `trim`)
│   ├── courses/example.json       sample course
│   ├── calibration.json           wheel-servo curves (created by `calibrate`)
│   ├── walker.db                  created on first run
│   └── runs/<run-id>/             truth.json, timeline.csv, rig_log.csv
├── firmware/
│   ├── rig_uno/rig_uno.ino                    Arduino: 5 servos, serial timeline player
│   ├── rig_bench/rig_bench.ino                same, for testing without the ESP32 (wheel flips/trims in the sketch)
│   ├── photo_counter_esp32/photo_counter_esp32.ino ESP32: counts wheel holes (calibration)
│   ├── part_tests/                            tiny sketches to check each part (see its README)
│   └── sensor_esp32/sensor_esp32.ino          ESP32 sensor kit: IMU 200 Hz + wheel notches + button, over USB
├── pipeline/                      Python on the laptop
│   ├── requirements.txt
│   ├── rig_driver.py              CLI: ports / spin / trim / calibrate / compile / run / watch / process
│   └── rig/
│       ├── course.py              geometry (mirrors web/lib/course.ts)
│       ├── compiler.py            course -> 50 Hz servo timeline + answer key
│       ├── sensor.py              records the ESP32 during a run (and a fake sensor for testing)
│       ├── process.py             sensor log -> detections (cracks, curbs, slopes, rough), path
│       ├── score.py               detections vs the answer key -> results.json
│       ├── calibration.py         wheel speed <-> pulse width
│       ├── calibrate.py           measures the wheel servos with the ESP32
│       ├── protocol.py            serial message format
│       ├── driver.py              streaming with flow control, logging
│       ├── fake_rig.py            software Arduino + ESP32 for testing without hardware
│       └── db.py                  SQLite helpers
└── web/                           Next.js (App Router), Node 22.13+
    ├── app/sim/page.tsx           the sandbox page
    ├── app/api/...                courses, runs, run status, abort
    ├── components/sim/            SimEditor.tsx, Plane.tsx, glyphs.tsx
    └── lib/                       types, course geometry + checks, validation, SQLite
```

## Wiring

**Rig controller (Arduino Uno R3)**

| Uno pin | Connects to |
|---|---|
| D5 | Left wheel continuous servo (FS90R), orange wire |
| D6 | Right wheel continuous servo, orange wire |
| D9 | Deck yaw servo (SG90), orange wire |
| D10 | Deck pitch servo (SG90), orange wire |
| D11 | Deck roll servo (SG90, optional), orange wire |
| GND | Breadboard power module GND |
| USB-B | Laptop |

Every servo's red wire goes to the power module's 5 V rail and brown wire to its GND rail.
The power module (jumper at 5 V) is fed by a phone charger or a spare laptop USB port.
Leave D0/D1 free.

**Sensor kit (ESP32)**: MPU-9250 on GPIO 21 (SDA) / 22 (SCL) / 19 (INT); wheel photoresistors
on GPIO 34 (left) / 35 (right), each wired 3V3 → photoresistor → GPIO with 10 kΩ from GPIO to GND;
wheel LEDs from VIN through 220 Ω, always on; button on GPIO 18 to GND.

## Setup

**Arduino:** open `firmware/rig_uno/rig_uno.ino`, board **Arduino Uno**, upload. No extra
libraries. Serial Monitor at 115200 shows `READY,24`; close it afterwards.

**ESP32 (for calibration):** open `firmware/photo_counter_esp32/photo_counter_esp32.ino`, board
**ESP32 Dev Module**, upload. Serial Monitor shows `H,<ms>,<left>,<right>,...` lines; the counts
go up by 4 per turn as you turn each wheel by hand. Close it afterwards. Not counting? Check the
beams with `firmware/part_tests/photo_test` first.

**Python** (3.10+)
```bash
cd pipeline
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python rig_driver.py ports
```

**Website** (Node 22.13+)
```bash
cd web
npm install
npm run dev            # http://localhost:3000/sim
```

## Calibrate the wheels (once, and after changing servos)

1. Check direction: `python rig_driver.py spin --port COM3 --left 1600 --right 1600`.
   Both wheels should roll forward. Note any that go backwards.
2. With each wheel's LED (behind) and photoresistor (in front) lined up on the hole circle,
   and the ESP32 plugged in: `python rig_driver.py calibrate --port COM3 --esp-port COM5`
   (add `--invert-left` and/or `--invert-right` for wheels that went backwards).
   Takes about 4 minutes. Use `rig_uno.ino` for this, not `rig_bench.ino`.
3. This writes `data/calibration.json`. Without it a typical FS90R curve is used, which can be
   10–15 % off.

## Running a course

```bash
cd web && npm run dev                                  # terminal 1
cd pipeline && python rig_driver.py watch --port COM3   # terminal 2
```
Draw a path, drag objects onto it, press **Run on rig**. No rig yet? `watch --fake --speedup 20`
(the fake rig also fakes the sensor kit, so you get results too).

**With the sensor kit:** upload `firmware/sensor_esp32` to the ESP32 and add its port:
`python rig_driver.py watch --port COM6 --esp-port COM7`. The driver records the ESP32 during
each run, then processes and scores it; the run panel on `/sim` shows what was found, missed or
falsely detected, the tilt felt over time, and the path measured from the wheels and gyro.
Files land in `data/runs/run-<id>/` (`imu.csv`, `wheels.csv`, `results.json`); re-analyse one
with `python rig_driver.py process ../data/runs/run-<id>`.

The analysis works out the IMU's axes itself: "up" from gravity during the 5 s standstill (which
also cancels any mounting tilt) and the tilt axis from the sync jolt at 5 s. Jolts are sized
against the 10° sync jolt (small crack ~0.6×, large ~1.2×, curb ~1.8×).

## Course limits (the site checks these)

- The yaw servo sweeps 180° in total, so the path's headings must stay within a 180° window
  (zig-zags, L-shapes and a single U-turn work; loops and spirals don't). If the turns lean one
  way, or a yaw trim eats into one side, the deck simply starts turned the other way.
- Wheel speed: about 1.2 turns/s, so keep the time scale at 2 or above (the default).
- Slopes and cross-slopes: about ±46 % (±25° of servo travel).

## First power-up checklist

- Servos take power from the power module only; its GND joins the Uno's GND.
- Deck not straight or level at rest? `python rig_driver.py trim --port COM3` nudges yaw/pitch/roll
  live and saves `data/deck.json`; the driver sends those trims every time it connects. A big yaw
  trim costs turning range on one side (the site and compiler check courses against it), so for
  more than ~15° re-seating the servo horn is better.
- Deck turns the wrong way in corners? Type `flip` in the trim tool (sets `yaw_sign` in `data/deck.json`).
- Jolts tilt the deck the wrong way? Flip `JOLT_DIR` in the sketch.
- Mount the yaw servo so its centre (90°) faces the rig's "forward".

## Serial protocol (laptop ↔ Arduino, 115200 baud)

| Direction | Message | Meaning |
| --- | --- | --- |
| Arduino → laptop | `READY,24` | booted (or reset by `H`); queue holds 24 lines |
| laptop → Arduino | `T,t_ms,left_us,right_us,yaw,pitch,roll,jolt,buzz` | one 20 ms timeline line |
| laptop → Arduino | `B` / `E` / `X` / `H` | start clock / end of stream / abort / reset |
| laptop → Arduino | `C,left_us,right_us` | calibration: drive wheels directly (0 = stop); replies `OK` |
| Arduino → laptop | `P,t_ms,executed,queued` | progress every 50 ms |
| Arduino → laptop | `DONE` or `DONE,aborted` | wheels stopped, deck centred |
| Arduino → laptop | `ERR,<text>` | bad line, queue overflow, or starved |

`left_us`/`right_us` = 0 stops that wheel (the servo is detached so it can't creep).
`jolt`: 0 none, 1 small crack, 2 large crack, 3 curb drop, 4 sync. `buzz`: 0 off, 1 light, 2 heavy.
