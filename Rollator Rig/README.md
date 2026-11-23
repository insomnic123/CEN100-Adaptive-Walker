# Rollator kit: rig sandbox

Draw a course in the browser, play it on the cardboard rig, and see what the sensor kit
detects. One repo, one Next.js site.

The rig is servos only, driven by an Arduino Uno R3 (a Mega works with the same pins):
two continuous servos spin the wheels, standard servos turn and tilt the IMU deck, and
crack/curb jolts are quick flicks of the pitch servo. The Arduino has no sensors: the
wheel servos are calibrated once with the ESP32 sensor kit's own Hall sensors.

```
rollator-rig/
├── README.md
├── data/                          shared by the site and Python
│   ├── schema.sql                 SQLite tables: courses, runs
│   ├── courses/example.json       sample course
│   ├── calibration.json           wheel-servo curves (created by `calibrate`)
│   ├── walker.db                  created on first run
│   └── runs/<run-id>/             truth.json, timeline.csv, rig_log.csv
├── firmware/
│   ├── rig_uno/rig_uno.ino                    Arduino: 5 servos, serial timeline player
│   ├── rig_bench/rig_bench.ino                same, for testing without the ESP32 (wheel flips/trims in the sketch)
│   ├── hall_counter_esp32/hall_counter_esp32.ino   ESP32: counts wheel magnets (calibration)
│   └── sensor_esp32/                          (next) full sensor kit: IMU + Hall + button, UDP
├── pipeline/                      Python on the laptop
│   ├── requirements.txt
│   ├── rig_driver.py              CLI: ports / spin / calibrate / compile / run / watch
│   └── rig/
│       ├── course.py              geometry (mirrors web/lib/course.ts)
│       ├── compiler.py            course -> 50 Hz servo timeline + answer key
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

**Sensor kit (ESP32)**: MPU-9250 on GPIO 21 (SDA) / 22 (SCL) / 19 (INT); A3144s on GPIO 34/35
with 10 kΩ pull-ups to 3V3; button on GPIO 18 to GND.

## Setup

**Arduino:** open `firmware/rig_uno/rig_uno.ino`, board **Arduino Uno**, upload. No extra
libraries. Serial Monitor at 115200 shows `READY,24`; close it afterwards.

**ESP32 (for calibration):** open `firmware/hall_counter_esp32/hall_counter_esp32.ino`, board
**ESP32 Dev Module**, upload. Serial Monitor shows `H,<ms>,0,0` lines that count up as you
wave a magnet past each sensor; close it afterwards.

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
2. With the sensor kit's A3144s facing the wheel magnets and the ESP32 plugged in:
   `python rig_driver.py calibrate --port COM3 --esp-port COM5` (add `--invert-left` and/or
   `--invert-right` for wheels that went backwards). Takes about 2.5 minutes.
3. This writes `data/calibration.json`. Without it a typical FS90R curve is used, which can be
   10–15 % off.

## Running a course

```bash
cd web && npm run dev                                  # terminal 1
cd pipeline && python rig_driver.py watch --port COM3   # terminal 2
```
Draw a path, drag objects onto it, press **Run on rig**. No rig yet? `watch --fake --speedup 20`.

## Course limits (the site checks these)

- The yaw servo covers ±90° from the first leg's direction, so a right turn must be followed
  by a left turn before another right (zig-zags and L-shapes work; loops and U-turns don't).
- Wheel speed: about 1.2 turns/s, so keep the time scale at 2 or above (the default).
- Slopes and cross-slopes: about ±46 % (±25° of servo travel).

## First power-up checklist

- Servos take power from the power module only; its GND joins the Uno's GND.
- Deck turns the wrong way in corners? Flip `YAW_SIGN` in `pipeline/rig/compiler.py`.
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
