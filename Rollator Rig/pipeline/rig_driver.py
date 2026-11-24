"""Drive the cardboard rig.

  python rig_driver.py ports                                   list serial ports
  python rig_driver.py spin --port COM3 --left 1600             check a wheel's direction (3 s)
  python rig_driver.py trim --port COM3                         straighten and level the deck at rest
  python rig_driver.py calibrate --port COM3 --esp-port COM5    measure the wheel servos once
  python rig_driver.py compile ../data/courses/example.json     check a course, no hardware
  python rig_driver.py run ../data/courses/example.json --port COM3
  python rig_driver.py run ../data/courses/example.json --fake --speedup 20
  python rig_driver.py watch --port /dev/ttyACM0                run whatever the website queues
  python rig_driver.py watch --port COM6 --esp-port COM7        ...and record + score the sensor kit
  python rig_driver.py process ../data/runs/run-12              re-analyse a recorded run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from rig import calibration, db
from rig.compiler import compile_course
from rig.driver import RigError, handshake, open_port, send_trims, stream

RUNS_DIR = db.ROOT / "data" / "runs"


def summarise(c) -> None:
    t = c.truth
    print(f"  route length   {t['route_length_m']:.2f} m (rounded corners)")
    print(f"  rig run time   {c.duration_s:.0f} s at time scale {t['time_scale']:g}")
    print(f"  timeline       {len(c.lines)} lines at {1 / t['tick_s']:.0f} Hz")
    print(f"  peak wheel     {t['peak_wheel_rps']:.2f} turns/s"
          f" ({'calibrated' if t['wheel_calibrated'] else 'typical curve, not calibrated'})")
    for o in t["objects"]:
        where = f"s={o['route_s']:.2f} m" if "route_s" in o else f"s={o['route_s0']:.2f}-{o['route_s1']:.2f} m"
        print(f"  {o['type']:<6} {where}" + (f"  at rig t={o['rig_t_s']:.1f} s" if "rig_t_s" in o else ""))
    for w in c.warnings:
        print(f"  warning: {w}")
    for e in c.errors:
        print(f"  ERROR: {e}")


def cmd_ports(_args) -> int:
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for p in ports:
        print(f"{p.device:<20} {p.description}")
    if not ports:
        print("no serial ports found; is the Mega plugged in?")
    return 0


def cmd_spin(args) -> int:
    """Spin the wheels briefly at given pulse widths, to see which way 'forward' is."""
    link = open_port(args.port, args.fake)
    try:
        handshake(link)
        link.write(f"C,{args.left},{args.right}\n".encode())
        time.sleep(args.seconds)
        link.write(b"C,0,0\n")
        time.sleep(0.2)
    finally:
        link.close()
    print("Each wheel should roll forward (the way the rig 'walks'). If one went backwards:\n"
          "  rig_bench.ino: flip INVERT_LEFT / INVERT_RIGHT in the sketch and re-upload.\n"
          "  rig_uno.ino:   calibrate with --invert-left / --invert-right for that wheel.")
    return 0


def cmd_calibrate(args) -> int:
    from rig import calibrate
    if args.fake:
        from rig.fake_rig import FakeRig, FakeWheelCounter
        rig = FakeRig(speedup=args.speedup)
        esp = FakeWheelCounter(rig, speedup=args.speedup)
    else:
        import serial
        if not args.port or not args.esp_port:
            print("need --port (Arduino) and --esp-port (ESP32 running photo_counter_esp32)")
            return 1
        rig = serial.Serial(args.port, 115200, timeout=0.05)
        esp = serial.Serial(args.esp_port, 115200, timeout=0.2)
    try:
        cal = calibrate.run(rig, esp, {"left": args.invert_left, "right": args.invert_right})
    except RigError as e:
        print(f"calibration failed: {e}")
        return 1
    finally:
        rig.close()
        esp.close()
    calibration.save(cal)
    print(f"saved {calibration.CAL_PATH}")
    print(f"top speed: {calibration.max_rps(cal):.2f} turns/s per wheel")
    return 0


def cmd_compile(args) -> int:
    c = compile_course(args.course)
    summarise(c)
    return 1 if c.errors else 0


def start_sensor(args, run_dir):
    """Starts recording the ESP32 (if --esp-port), waiting until IMU data arrives."""
    if not getattr(args, "esp_port", None):
        return None
    from rig.sensor import SensorRecorder
    rec = SensorRecorder(args.esp_port, run_dir)
    deadline = time.monotonic() + 4
    while rec.imu_lines() < 20 and time.monotonic() < deadline:
        time.sleep(0.05)
    if rec.imu_lines() < 20:
        why = "; ".join(rec.problems) or "is sensor_esp32 uploaded, and is that the ESP32's port?"
        print(f"  warning: no IMU data from the ESP32 ({why}); running without the sensor kit")
    return rec


def finish_sensor(args, rec, run_dir) -> str | None:
    """Stops recording (or fakes the sensor log on the fake rig), then processes and scores."""
    from rig.score import analyse_run, summary_line
    if rec is not None:
        time.sleep(0.5)                                  # catch the last samples
        rec.stop()
    elif getattr(args, "fake", False):
        from rig.sensor import fake_sensor
        fake_sensor(run_dir)
    else:
        return None
    res = analyse_run(run_dir)
    line = summary_line(res)
    print(f"  sensor kit: {line}")
    return line


def cmd_process(args) -> int:
    from rig.score import analyse_run, summary_line
    res = analyse_run(args.run_dir)
    print(summary_line(res))
    for o in res.get("objects", []):
        extra = f" ({o['note']})" if o.get("note") else ""
        when = f"{o['timing_ms']:+d} ms" if o.get("timing_ms") is not None else ""
        print(f"  {o['status']:<11} {o['label']:<22} {when}{extra}")
    for f in res.get("false_alarms", []):
        print(f"  false alarm  {f['kind']} at {f.get('t', f.get('t0'))} s")
    print(f"written {Path(args.run_dir) / 'results.json'}")
    return 0 if res.get("ok") else 1


def cmd_run(args) -> int:
    c = compile_course(args.course)
    summarise(c)
    if c.errors:
        return 1
    run_dir = RUNS_DIR / datetime.now().strftime("cli-%Y%m%d-%H%M%S")
    link = open_port(args.port, args.fake, args.speedup)
    rec = None
    try:
        rec = start_sensor(args, run_dir)
        res = stream(link, c, run_dir, on_progress=lambda s: print(f"\r  progress {s:6.2f} m", end=""),
                     on_begin=rec.mark_begin if rec else None)
    except KeyboardInterrupt:
        link.write(b"X\n")
        print("\n  aborted")
        if rec:
            rec.stop()
        return 1
    finally:
        link.close()
    print(f"\n  {'aborted' if res['aborted'] else 'done'}; logs in {run_dir}")
    if not res["aborted"] and (rec is not None or args.fake):
        finish_sensor(args, rec, run_dir)
    elif rec:
        rec.stop()
    return 0


def cmd_watch(args) -> int:
    con = db.connect()
    print("watching for runs queued by the website (Ctrl+C to stop)")
    link = None
    try:
        while True:
            row = db.next_queued_run(con)
            if row is None:
                time.sleep(0.5)
                continue
            run_id = row["id"]
            print(f"run {run_id}: compiling")
            c = compile_course(json.loads(row["course_json"]))
            if c.errors:
                db.set_run(con, run_id, status="error", message="; ".join(c.errors),
                           finished_at=datetime.now().isoformat(timespec="seconds"))
                continue
            db.set_run(con, run_id, status="running", duration_s=c.duration_s,
                       started_at=datetime.now().isoformat(timespec="seconds"),
                       message="; ".join(c.warnings) or None)
            try:
                if link is None:
                    link = open_port(args.port, args.fake, args.speedup)
                run_dir = RUNS_DIR / f"run-{run_id}"
                rec = start_sensor(args, run_dir)
                try:
                    res = stream(link, c, run_dir,
                                 on_progress=lambda s: db.set_run(con, run_id, progress_s=s),
                                 should_abort=lambda: db.abort_requested(con, run_id),
                                 on_begin=rec.mark_begin if rec else None)
                except BaseException:
                    if rec:
                        rec.stop()
                    raise
                summary = None
                if not res["aborted"] and (rec is not None or args.fake):
                    db.set_run(con, run_id, message="processing the sensor data")
                    summary = finish_sensor(args, rec, run_dir)
                elif rec:
                    rec.stop()
                db.set_run(con, run_id, status="aborted" if res["aborted"] else "done",
                           message=summary, finished_at=datetime.now().isoformat(timespec="seconds"))
                print(f"run {run_id}: {'aborted' if res['aborted'] else 'done'}")
            except (RigError, OSError) as e:
                db.set_run(con, run_id, status="error", message=str(e),
                           finished_at=datetime.now().isoformat(timespec="seconds"))
                print(f"run {run_id}: error: {e}")
                if link is not None:
                    link.close()
                    link = None
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if link is not None:
            link.close()
    return 0


TRIM_HELP = """Adjust until the deck faces straight forward and sits level:
  y+ / y-     nudge yaw by 2 degrees        y 35    set yaw trim to 35
  p+ / p-     nudge pitch by 2 degrees      p -30   set pitch trim to -30
  r+ / r-     nudge roll by 2 degrees       r 0     set roll trim to 0
  flip        reverse the yaw direction (if the deck turns the wrong way in corners)
  save        write data/deck.json          q       quit"""


def cmd_trim(args) -> int:
    from rig import deck
    d = deck.load()
    link = open_port(args.port, args.fake)
    saved = dict(d)
    try:
        handshake(link, trims=d)
        print(TRIM_HELP)
        keys = {"y": "yaw_trim", "p": "pitch_trim", "r": "roll_trim"}
        while True:
            try:
                cmd = input(f"yaw {d['yaw_trim']:+d}  pitch {d['pitch_trim']:+d}  roll {d['roll_trim']:+d}"
                            f"  (yaw direction {d['yaw_sign']:+d}) > ").strip().lower()
            except EOFError:
                cmd = "q"
            if cmd in ("q", "quit", "exit"):
                if d != saved and input("save changes to data/deck.json? [y/n] ").strip().lower() == "y":
                    deck.save(d)
                    print(f"saved {deck.DECK_PATH}")
                break
            if cmd == "save":
                deck.save(d)
                saved = dict(d)
                print(f"saved {deck.DECK_PATH}")
                continue
            if cmd == "flip":
                d["yaw_sign"] = -d["yaw_sign"]
                print("yaw direction flipped (used by the compiler; the deck doesn't move now)")
                continue
            key = keys.get(cmd[:1]) if cmd else None
            rest = cmd[1:].replace("aw", "", 1).replace("itch", "", 1).replace("oll", "", 1).strip()
            if key and rest in ("+", "-"):
                d[key] += 2 if rest == "+" else -2
            elif key:
                try:
                    d[key] = int(rest)
                except ValueError:
                    print(TRIM_HELP)
                    continue
            else:
                print(TRIM_HELP)
                continue
            if abs(d[key]) > deck.TRIM_LIMIT:
                d[key] = max(-deck.TRIM_LIMIT, min(deck.TRIM_LIMIT, d[key]))
                print(f"  capped at ±{deck.TRIM_LIMIT}; beyond that, re-seat the servo horn")
            send_trims(link, d)
        lo, hi = deck.heading_range(d)
        print(f"the deck can now turn {max(hi, 0):.0f} degrees left and {max(-lo, 0):.0f} right "
              "of the starting direction")
    finally:
        link.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports")
    p = sub.add_parser("spin")
    p.add_argument("--port")
    p.add_argument("--left", type=int, default=1600, help="pulse width in us, 0 = stop")
    p.add_argument("--right", type=int, default=1600)
    p.add_argument("--seconds", type=float, default=3)
    p.add_argument("--fake", action="store_true")
    p = sub.add_parser("trim")
    p.add_argument("--port")
    p.add_argument("--fake", action="store_true")
    p = sub.add_parser("calibrate")
    p.add_argument("--port", help="the Arduino")
    p.add_argument("--esp-port", help="the ESP32 running firmware/photo_counter_esp32")
    p.add_argument("--invert-left", action="store_true")
    p.add_argument("--invert-right", action="store_true")
    p.add_argument("--fake", action="store_true")
    p.add_argument("--speedup", type=float, default=1.0)
    p = sub.add_parser("compile")
    p.add_argument("course", type=Path)
    p = sub.add_parser("process")
    p.add_argument("run_dir", type=Path)
    for name in ("run", "watch"):
        p = sub.add_parser(name)
        if name == "run":
            p.add_argument("course", type=Path)
        p.add_argument("--esp-port", help="the ESP32 running firmware/sensor_esp32: record and score the run")
        p.add_argument("--port", help="e.g. COM3, /dev/ttyACM0, /dev/cu.usbmodem1101")
        p.add_argument("--fake", action="store_true", help="use the software rig instead of the Mega")
        p.add_argument("--speedup", type=float, default=1.0, help="fake rig only: run faster than real time")
    args = ap.parse_args()
    try:
        return {"ports": cmd_ports, "spin": cmd_spin, "trim": cmd_trim, "calibrate": cmd_calibrate,
                "compile": cmd_compile, "process": cmd_process,
                "run": cmd_run, "watch": cmd_watch}[args.cmd](args)
    except RigError as e:
        print(f"error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
