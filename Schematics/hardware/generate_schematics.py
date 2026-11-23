"""Generates the two KiCad schematics for the rollator test bench.

  rig_controller/rig_controller.kicad_sch   Arduino Uno R3 rig: 2 continuous wheel servos,
                                             1 stepper turntable, pitch/tapper/roll servos,
                                             2 feedback A3144s
  sensor_kit/sensor_kit.kicad_sch           ESP32 sensor kit: MPU-9250, 2 A3144s, button

Written in KiCad 7 format (opens in KiCad 7, 8 and 9). Run with: python3 generate_schematics.py
Library symbols are copied from KiCad's standard library when available (Arduino, servo,
resistor, capacitor, push button, power symbols); module symbols are drawn here.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
KICAD_SYMBOLS = Path("/usr/share/kicad/symbols")
G = 2.54
FONT = "(effects (font (size 1.27 1.27)))"
FONT_HIDE = "(effects (font (size 1.27 1.27)) hide)"


def uid() -> str:
    return str(uuid.uuid4())


def fmt(v: float) -> str:
    v = round(v, 4)
    return str(int(v)) if v == int(v) else f"{v}"


# ----------------------------------------------------------------------------- library symbols
def lib_block(lib: str, name: str) -> str:
    text = (KICAD_SYMBOLS / f"{lib}.kicad_sym").read_text()
    i = text.find(f'(symbol "{name}"')
    if i < 0:
        raise KeyError(f"{lib}:{name}")
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    raise ValueError("unbalanced")


PIN_RE = re.compile(r'\(pin (\w+) \w+ \(at ([-\d.]+) ([-\d.]+) (\d+)\) \(length [\d.]+\)(?: hide)?\s*'
                    r'\(name "([^"]*)".*?\(number "([^"]*)"', re.S)
PROP_RE = re.compile(r'\(property "(Reference|Value)" "[^"]*" \(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)')


class Sym:
    """A library symbol: its embedded definition plus pin and field positions."""

    def __init__(self, lib_id: str, body: str, ref_prefix: str):
        self.lib_id = lib_id
        self.body = body
        self.ref_prefix = ref_prefix
        self.pins = {num: (float(x), float(y), int(a), name, etype)
                     for etype, x, y, a, name, num in PIN_RE.findall(body)}
        self.props = {k: (float(x), float(y), float(a)) for k, x, y, a in PROP_RE.findall(body)}
        self.is_power = "(power)" in body[:200]


def from_kicad(lib: str, name: str, ref_prefix: str) -> Sym:
    body = lib_block(lib, name)
    body = body.replace(f'(symbol "{name}"', f'(symbol "{lib}:{name}"', 1)
    return Sym(f"{lib}:{name}", body, ref_prefix)


def box_symbol(name: str, ref_prefix: str, value: str, left: list, right: list,
               top: list = (), bottom: list = (), half_w: float = 7.62, footprint: str = "",
               edge_offset: float = 0.0) -> Sym:
    """Rectangle with pins. Side lists hold (number, name, type) or None for a blank row;
    top/bottom hold supply and ground pins so power symbols stand vertically."""
    pad_t = 2 if top else 0             # room for vertical pin names inside the box
    pad_b = 2 if bottom else 0
    n = max(len(left), len(right), 1) + pad_t + pad_b
    top_y = G * ((n + 1) // 2 + 1)
    bot_y = top_y - G * (n + 1)
    lines = [
        f'(symbol "Rollator:{name}" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)',
        f'  (property "Reference" "{ref_prefix}" (at {fmt(-half_w)} {fmt(top_y + (5.08 if top else 1.27))} 0)'
        f' (effects (font (size 1.27 1.27)) (justify left)))',
        f'  (property "Value" "{value}" (at {fmt(-half_w)} {fmt(bot_y - (5.08 if bottom else 1.27))} 0)'
        f' (effects (font (size 1.27 1.27)) (justify left)))',
        f'  (property "Footprint" "{footprint}" (at 0 0 0) {FONT_HIDE})',
        f'  (property "Datasheet" "" (at 0 0 0) {FONT_HIDE})',
        f'  (symbol "{name}_0_1" (rectangle (start {fmt(-half_w)} {fmt(top_y)}) (end {fmt(half_w)} {fmt(bot_y)})'
        f' (stroke (width 0.254) (type default)) (fill (type background))))',
        f'  (symbol "{name}_1_1"',
    ]

    def pin(num, pname, etype, x, y, ang):
        lines.append(f'    (pin {etype} line (at {fmt(x)} {fmt(y)} {ang}) (length 2.54)'
                     f' (name "{pname}" {FONT}) (number "{num}" {FONT}))')

    for side, pins in (("L", left), ("R", right)):
        for i, p in enumerate(pins):
            if p is None:
                continue
            y = top_y - G * (i + 1 + pad_t)
            pin(*p, -(half_w + G) if side == "L" else half_w + G, y, 0 if side == "L" else 180)
    for edge, pins in (("T", top), ("B", bottom)):
        k = len(pins)
        for i, p in enumerate(pins):
            x = G * 2 * (i - (k - 1) / 2) + edge_offset
            x = round(x / G) * G
            pin(*p, x, top_y + G if edge == "T" else bot_y - G, 270 if edge == "T" else 90)
    lines.append("  )")
    lines.append(")")
    return Sym(f"Rollator:{name}", "\n".join(lines), ref_prefix)


def power_symbol(net: str, base: str = "+5V") -> Sym:
    """A power symbol whose net is `net`, drawn like KiCad's +5V / GND."""
    body = lib_block("power", base)
    body = body.replace(f'(symbol "{base}"', f'(symbol "Rollator:{net}"', 1)
    body = body.replace(f'(symbol "{base}_', f'(symbol "{net}_')
    body = body.replace(f'(property "Value" "{base}"', f'(property "Value" "{net}"', 1)
    body = body.replace(f'(name "{base}"', f'(name "{net}"')
    return Sym(f"Rollator:{net}", body, "#PWR")


# ----------------------------------------------------------------------------- sheet builder
POWER_NETS = {"GND", "+5V", "+3V3", "+5V_MOT"}
OUT = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}   # outward direction on the sheet
STUB = 2 * G


class Sheet:
    def __init__(self, project: str, title: str, comments: list[str], paper: str = "A3"):
        self.project = project
        self.title = title
        self.comments = comments
        self.paper = paper
        self.root = uid()
        self.syms: dict[str, Sym] = {}
        self.items: list[str] = []
        self.pwr_n = 0
        self.power_syms = {
            "GND": from_kicad("power", "GND", "#PWR"),
            "+5V": from_kicad("power", "+5V", "#PWR"),
            "+3V3": from_kicad("power", "+3V3", "#PWR"),
            "+5V_MOT": power_symbol("+5V_MOT"),
        }
        self.flag = from_kicad("power", "PWR_FLAG", "#FLG")

    def use(self, sym: Sym) -> Sym:
        self.syms[sym.lib_id] = sym
        return sym

    def _instance(self, sym: Sym, ref: str, value: str, x: float, y: float, rot: int = 0,
                  dnp: bool = False, footprint: str = "", value_at=None) -> None:
        self.use(sym)
        props = []
        for key, val in (("Reference", ref), ("Value", value)):
            px, py, pa = sym.props.get(key, (0, 0, 0))
            c, s_ = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[rot]
            px, py = px * c - py * s_, px * s_ + py * c
            eff = FONT_HIDE if (sym.is_power and key == "Reference") else FONT
            if key == "Value" and value_at is not None:
                props.append(f'(property "Value" "{val}" (at {fmt(x + value_at[0])} {fmt(y + value_at[1])} 0)'
                             f' (effects (font (size 1.27 1.27)) (justify left)))')
                continue
            props.append(f'(property "{key}" "{val}" (at {fmt(x + px)} {fmt(y - py)} {fmt(pa)}) {eff})')
        props.append(f'(property "Footprint" "{footprint}" (at {fmt(x)} {fmt(y)} 0) {FONT_HIDE})')
        props.append(f'(property "Datasheet" "" (at {fmt(x)} {fmt(y)} 0) {FONT_HIDE})')
        pins = " ".join(f'(pin "{n}" (uuid {uid()}))' for n in sym.pins)
        self.items.append(
            f'(symbol (lib_id "{sym.lib_id}") (at {fmt(x)} {fmt(y)} {rot}) (unit 1)'
            f' (in_bom {"no" if sym.is_power else "yes"}) (on_board {"no" if sym.is_power else "yes"})'
            f' (dnp {"yes" if dnp else "no"}) (uuid {uid()})\n  ' + "\n  ".join(props) + f"\n  {pins}\n"
            f'  (instances (project "{self.project}" (path "/{self.root}" (reference "{ref}") (unit 1)))))')

    def wire(self, a, b) -> None:
        self.items.append(f'(wire (pts (xy {fmt(a[0])} {fmt(a[1])}) (xy {fmt(b[0])} {fmt(b[1])}))'
                          f' (stroke (width 0) (type default)) (uuid {uid()}))')

    def label(self, net: str, at, direction) -> None:
        ang = {(-1, 0): 180, (1, 0): 0, (0, -1): 90, (0, 1): 270}[direction]
        just = "right" if ang in (180, 270) else "left"
        self.items.append(f'(label "{net}" (at {fmt(at[0])} {fmt(at[1])} {ang}) (fields_autoplaced)'
                          f' (effects (font (size 1.27 1.27)) (justify {just} bottom)) (uuid {uid()}))')

    def power(self, net: str, at, direction) -> None:
        sym = self.power_syms[net]
        self.pwr_n += 1
        self._instance(sym, f"#PWR{self.pwr_n:02d}", net, at[0], at[1], 0)

    def no_connect(self, at) -> None:
        self.items.append(f'(no_connect (at {fmt(at[0])} {fmt(at[1])}) (uuid {uid()}))')

    def place(self, sym: Sym, ref: str, value: str, x: float, y: float, nets: dict[str, str],
              dnp: bool = False, footprint: str = "", value_at=None) -> None:
        """Place a symbol and attach each pin to a net (or 'NC'). Unlisted pins become no-connects.
        Power pins get a power symbol; several pins on the same edge and net share one."""
        self._instance(sym, ref, value, x, y, dnp=dnp, footprint=footprint, value_at=value_at)
        groups: dict[tuple, list] = {}
        for num, (px, py, ang, _name, _etype) in sym.pins.items():
            end = (x + px, y - py)
            net = nets.get(num, "NC")
            if net == "NC":
                self.no_connect(end)
                continue
            d = OUT[ang]
            stub = (end[0] + d[0] * STUB, end[1] + d[1] * STUB)
            self.wire(end, stub)
            if net in POWER_NETS:
                groups.setdefault((net, d, stub[1] if d[1] else stub[0]), []).append(stub)
            else:
                self.label(net, stub, d)
        for (net, d, _), stubs in groups.items():
            stubs.sort()
            for a, b in zip(stubs, stubs[1:]):          # endpoint-to-endpoint segments
                self.wire(a, b)
            for mid in stubs[1:-1]:
                self.items.append(f'(junction (at {fmt(mid[0])} {fmt(mid[1])}) (diameter 0) (color 0 0 0 0) (uuid {uid()}))')
            self.power(net, stubs[len(stubs) // 2], d)

    def pwr_flag(self, net: str, x: float, y: float) -> None:
        """PWR_FLAG tells KiCad's electrical check that this net really is powered."""
        self.pwr_n += 1
        if net == "GND":
            self._instance(self.flag, f"#FLG{self.pwr_n:02d}", "PWR_FLAG", x, y)
            self.wire((x, y), (x, y + STUB))
            self.power(net, (x, y + STUB), (0, 1))
        else:
            self.power(net, (x, y), (0, -1))
            self.wire((x, y), (x, y + STUB))
            self._instance(self.flag, f"#FLG{self.pwr_n:02d}", "PWR_FLAG", x, y + STUB, rot=180)

    def text(self, s: str, x: float, y: float, size: float = 1.27) -> None:
        s = s.replace('"', "'")
        self.items.append(f'(text "{s}" (at {fmt(x)} {fmt(y)} 0)'
                          f' (effects (font (size {size} {size})) (justify left bottom)) (uuid {uid()}))')

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        comments = " ".join(f'(comment {i + 1} "{c}")' for i, c in enumerate(self.comments[:4]))
        out = [
            "(kicad_sch (version 20230121) (generator eeschema)",
            f"  (uuid {self.root})",
            f'  (paper "{self.paper}")',
            f'  (title_block (title "{self.title}") (date "2026-11-21") (rev "1") {comments})',
            "  (lib_symbols",
            *["    " + s.body.replace("\n", "\n    ") for s in self.syms.values()],
            "  )",
            *["  " + it for it in self.items],
            '  (sheet_instances (path "/" (page "1")))',
            ")",
        ]
        path.write_text("\n".join(out) + "\n")
        # minimal project file so KiCad opens it as a project
        pro = path.with_suffix(".kicad_pro")
        if not pro.exists():
            pro.write_text('{\n  "meta": {"filename": "%s", "version": 1},\n  "schematic": {}\n}\n' % pro.name)


# ----------------------------------------------------------------------------- parts
def a3144() -> Sym:
    return box_symbol("A3144", "U", "A3144", [], [("3", "OUT", "open_collector")],
                      top=[("1", "VCC", "power_in")], bottom=[("2", "GND", "power_in")],
                      half_w=5.08, footprint="Package_TO_SOT_THT:TO-92_Inline")


def servo_sym() -> Sym:
    return box_symbol("Servo", "M", "Servo", [("1", "PWM", "input")], [],
                      top=[("2", "+", "power_in")], bottom=[("3", "-", "power_in")], half_w=5.08)


def rig_controller() -> Sheet:
    sh = Sheet("rig_controller", "Rollator test rig: rig controller (Arduino Uno R3)", [
        "Wheels: continuous servos with Hall feedback. Turntable: 28BYJ-48 via ULN2003 board.",
        "Motor power (+5V_MOT) comes from the breadboard power module, never from the Uno.",
        "Only GND is shared between the Uno and the motor supply.",
        "Firmware: firmware/rig_uno (continuous-servo version)",
    ])
    uno = from_kicad("MCU_Module", "Arduino_UNO_R3", "A")
    servo = servo_sym()
    res = from_kicad("Device", "R", "R")
    cap = from_kicad("Device", "C", "C")
    hall = a3144()
    uln = box_symbol(
        "ULN2003_Module", "U", "ULN2003 driver board",
        [("1", "IN1", "input"), ("2", "IN2", "input"), ("3", "IN3", "input"), ("4", "IN4", "input")],
        [("7", "OUT1", "open_collector"), ("8", "OUT2", "open_collector"), ("9", "OUT3", "open_collector"),
         ("10", "OUT4", "open_collector"), ("11", "MOTOR +", "passive")],
        top=[("5", "+", "power_in")], bottom=[("6", "-", "power_in")], half_w=7.62)
    stepper = box_symbol(
        "Stepper_28BYJ48", "M", "28BYJ-48 stepper",
        [("1", "Blue", "passive"), ("2", "Pink", "passive"), ("3", "Yellow", "passive"),
         ("4", "Orange", "passive"), ("5", "Red (common)", "passive")], [], half_w=10.16)
    pmod = box_symbol("Power_Module_5V", "J", "Breadboard power module", [], [],
                      top=[("1", "+5V", "power_out")], bottom=[("2", "GND", "power_out")], half_w=10.16)

    # Arduino Uno (pin numbers are those of KiCad's Arduino_UNO_R3 symbol)
    sh.place(uno, "A1", "Arduino Uno R3", 180.34, 148.59, {
        "17": "FB_L", "18": "FB_R",                     # D2, D3 (interrupt pins)
        "20": "WHEEL_L", "21": "WHEEL_R",               # D5, D6
        "24": "PITCH", "25": "TAP", "26": "ROLL",       # D9, D10, D11
        "9": "YAW_IN1", "10": "YAW_IN2", "11": "YAW_IN3", "12": "YAW_IN4",   # A0-A3
        "5": "+5V", "29": "GND", "6": "GND", "7": "GND",
    }, value_at=(5.08, 35.56))
    sh.text("D0/D1 carry USB serial to the laptop: keep them free.", 142.24, 195.58)
    sh.text("USB-B: laptop (power for the Uno + serial commands)", 142.24, 198.12)

    # motor supply
    sh.place(pmod, "J1", "Breadboard power module (jumper at 5V)", 50.8, 58.42, {"1": "+5V_MOT", "2": "GND"})
    sh.text("Feed it from a phone charger (2 A+) or a spare laptop USB port.", 30.48, 81.28)
    sh.text("Motors and servos only. Its GND joins the Uno's GND.", 30.48, 83.82)
    sh.pwr_flag("+5V_MOT", 99.06, 50.8)
    sh.pwr_flag("GND", 111.76, 50.8)

    # turntable stepper
    sh.place(uln, "U1", "ULN2003 driver board", 299.72, 58.42, {
        "1": "YAW_IN1", "2": "YAW_IN2", "3": "YAW_IN3", "4": "YAW_IN4",
        "5": "+5V_MOT", "6": "GND",
        "7": "STEP_BLUE", "8": "STEP_PINK", "9": "STEP_YELLOW", "10": "STEP_ORANGE", "11": "STEP_RED"})
    sh.place(stepper, "M6", "28BYJ-48 (turntable)", 365.76, 58.42, {
        "1": "STEP_BLUE", "2": "STEP_PINK", "3": "STEP_YELLOW", "4": "STEP_ORANGE", "5": "STEP_RED"})
    sh.text("The stepper's plug goes straight into the board's white socket;", 279.4, 83.82)
    sh.text("OUT1-OUT4 and MOTOR + show what that socket connects.", 279.4, 86.36)

    # servos: two columns
    servos = [("M1", "FS90R continuous (left wheel)", "WHEEL_L", False, 279.4, 114.3),
              ("M2", "FS90R continuous (right wheel)", "WHEEL_R", False, 345.44, 114.3),
              ("M3", "SG90 (deck pitch)", "PITCH", False, 279.4, 165.1),
              ("M4", "SG90 (tapper)", "TAP", False, 345.44, 165.1),
              ("M5", "SG90 (deck roll, optional)", "ROLL", True, 279.4, 215.9)]
    for ref, val, net, dnp, x, y in servos:
        sh.place(servo, ref, val, x, y, {"1": net, "2": "+5V_MOT", "3": "GND"}, dnp=dnp)
    sh.text("Servos: red to +5V_MOT, brown to GND, orange (signal) to the Uno.", 312.42, 210.82)
    sh.text("M5 is crossed out because it is optional (cross-slope only).", 312.42, 213.36)

    # wheel feedback Hall sensors (Uno side); pull-ups to the Uno's 5 V
    for i, (u, r, c, net, side) in enumerate([("U2", "R1", "C1", "FB_L", "left"), ("U3", "R2", "C2", "FB_R", "right")]):
        y = 129.54 + i * 45.72
        sh.place(hall, u, f"A3144 ({side} wheel feedback)", 60.96, y, {"1": "+5V", "2": "GND", "3": net})
        sh.place(res, r, "10k", 86.36, y - 5.08, {"1": "+5V", "2": net})
        sh.place(cap, c, "100nF (optional)", 35.56, y, {"1": "+5V", "2": "GND"})
    sh.text("Feedback A3144s read the same wheel magnets as the sensor kit's A3144s,", 25.4, 205.74)
    sh.text("but connect only to the Uno. Pull-ups go to the Uno's 5V (fine on a 5 V board).", 25.4, 208.28)
    return sh


def sensor_kit() -> Sheet:
    sh = Sheet("sensor_kit", "Rollator sensor kit (ESP32)", [
        "Same wiring on the rig and on a real walker.",
        "Hall pull-ups go to 3V3: ESP32 pins are not 5 V tolerant.",
        "Only the ESP32 pins in use are drawn; match names to your board's silkscreen.",
        "Firmware: firmware/sensor_esp32",
    ], paper="A4")
    esp = box_symbol(
        "ESP32_DevKit", "U", "ESP32 DevKit",
        [], [("4", "GPIO18", "bidirectional"), ("5", "GPIO19", "bidirectional"), ("6", "GPIO21 (SDA)", "bidirectional"),
             ("7", "GPIO22 (SCL)", "bidirectional"), ("8", "GPIO34 (input only)", "input"),
             ("9", "GPIO35 (input only)", "input")],
        top=[("1", "3V3", "power_out"), ("2", "VIN", "power_out")], bottom=[("3", "GND", "power_in")],
        half_w=17.78, edge_offset=-10.16)
    mpu = box_symbol(
        "MPU9250_Breakout", "U", "MPU-9250 breakout",
        [("4", "SDA", "bidirectional"), ("3", "SCL", "input"), ("8", "INT", "output"), None,
         ("5", "EDA", "bidirectional"), ("6", "ECL", "output")],
        [("9", "NCS", "input"), None, ("7", "AD0", "input"), None, ("10", "FSYNC", "input"), None],
        top=[("1", "VCC", "power_in")], bottom=[("2", "GND", "power_in")], half_w=7.62)
    res = from_kicad("Device", "R", "R")
    cap = from_kicad("Device", "C", "C")
    btn = from_kicad("Switch", "SW_Push", "SW")
    hall = a3144()

    sh.place(esp, "U1", "ESP32 DevKit (ELEGOO kit)", 81.28, 81.28, {
        "1": "+3V3", "2": "+5V", "3": "GND",
        "4": "BTN", "5": "IMU_INT", "6": "SDA", "7": "SCL", "8": "HALL_L", "9": "HALL_R"})
    sh.text("Powered over USB-C (laptop or power bank).", 60.96, 114.3)
    sh.text("VIN carries the USB 5 V out to the Hall sensors.", 60.96, 116.84)

    sh.place(mpu, "U2", "MPU-9250 breakout (GY-9250)", 172.72, 50.8, {
        "1": "+3V3", "2": "GND", "3": "SCL", "4": "SDA", "5": "NC",
        "6": "NC", "7": "GND", "8": "IMU_INT", "9": "+3V3", "10": "GND"})
    sh.text("AD0 to GND = I2C address 0x68. NCS to 3V3 = I2C mode.", 154.94, 88.9)
    sh.text("Pin order here is for clarity; use the labels on your board.", 154.94, 91.44)

    for i, (u, r, c, net, side) in enumerate([("U3", "R1", "C1", "HALL_L", "left"), ("U4", "R2", "C2", "HALL_R", "right")]):
        x = 152.4 + i * 55.88
        sh.place(hall, u, f"A3144 ({side} wheel)", x, 116.84, {"1": "+5V", "2": "GND", "3": net})
        sh.place(res, r, "10k", x + 22.86, 111.76, {"1": "+3V3", "2": net})
        sh.place(cap, c, "100nF (optional)", x - 17.78, 116.84, {"1": "+5V", "2": "GND"})

    sh.place(btn, "SW1", "Push button (hard spot)", 45.72, 142.24, {"1": "BTN", "2": "GND"},
             value_at=(-12.7, -5.08))
    sh.text("GPIO18 uses the ESP32's internal pull-up (INPUT_PULLUP).", 30.48, 154.94)

    sh.pwr_flag("GND", 22.86, 40.64)
    sh.pwr_flag("+5V", 35.56, 40.64)
    sh.pwr_flag("+3V3", 48.26, 40.64)
    return sh


if __name__ == "__main__":
    rig_controller().save(HERE / "rig_controller" / "rig_controller.kicad_sch")
    sensor_kit().save(HERE / "sensor_kit" / "sensor_kit.kicad_sch")
    print("written to", HERE)
