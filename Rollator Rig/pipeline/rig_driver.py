"""Drive the cardboard rig.

  python rig_driver.py ports                                   list serial ports
  python rig_driver.py spin --port COM3 --left 1600             check a wheel's direction (3 s)
  python rig_driver.py calibrate --port COM3 --esp-port COM5    measure the wheel servos once
  python rig_driver.py compile ../data/courses/example.json     check a course, no hardware
  python rig_driver.py run ../data/courses/example.json --port COM3
  python rig_driver.py run ../data/courses/example.json --fake --speedup 20
  python rig_driver.py watch --port /dev/ttyACM0                run whatever the website queues
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
from rig.driver import RigError, handshake, open_port, stream

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
    print("Each wheel should roll forward (the way the rig 'walks'). If one went backwards,\n"
          "calibrate with --invert-left or --invert-right for that wheel.")
    return 0


def cmd_calibrate(args) -> int:
    from rig import calibrate
    if args.fake:
        from rig.fake_rig import FakeHallCounter, FakeRig
        rig = FakeRig(speedup=args.speedup)
        esp = FakeHallCounter(rig, speedup=args.speedup)
    else:
        import serial
        if not args.port or not args.esp_port:
            print("need --port (Arduino) and --esp-port (ESP32 running hall_counter_esp32)")
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


def cmd_run(args) -> int:
    c = compile_course(args.course)
    summarise(c)
    if c.errors:
        return 1
    run_dir = RUNS_DIR / datetime.now().strftime("cli-%Y%m%d-%H%M%S")
    link = open_port(args.port, args.fake, args.speedup)
    try:
        res = stream(link, c, run_dir, on_progress=lambda s: print(f"\r  progress {s:6.2f} m", end=""))
    except KeyboardInterrupt:
        link.write(b"X\n")
        print("\n  aborted")
        return 1
    finally:
        link.close()
    print(f"\n  {'aborted' if res['aborted'] else 'done'}; logs in {run_dir}")
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
                res = stream(link, c, RUNS_DIR / f"run-{run_id}",
                             on_progress=lambda s: db.set_run(con, run_id, progress_s=s),
                             should_abort=lambda: db.abort_requested(con, run_id))
                db.set_run(con, run_id, status="aborted" if res["aborted"] else "done",
                           finished_at=datetime.now().isoformat(timespec="seconds"))
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
    p = sub.add_parser("calibrate")
    p.add_argument("--port", help="the Arduino")
    p.add_argument("--esp-port", help="the ESP32 running firmware/hall_counter_esp32")
    p.add_argument("--invert-left", action="store_true")
    p.add_argument("--invert-right", action="store_true")
    p.add_argument("--fake", action="store_true")
    p.add_argument("--speedup", type=float, default=1.0)
    p = sub.add_parser("compile")
    p.add_argument("course", type=Path)
    for name in ("run", "watch"):
        p = sub.add_parser(name)
        if name == "run":
            p.add_argument("course", type=Path)
        p.add_argument("--port", help="e.g. COM3, /dev/ttyACM0, /dev/cu.usbmodem1101")
        p.add_argument("--fake", action="store_true", help="use the software rig instead of the Mega")
        p.add_argument("--speedup", type=float, default=1.0, help="fake rig only: run faster than real time")
    args = ap.parse_args()
    return {"ports": cmd_ports, "spin": cmd_spin, "calibrate": cmd_calibrate, "compile": cmd_compile,
            "run": cmd_run, "watch": cmd_watch}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
