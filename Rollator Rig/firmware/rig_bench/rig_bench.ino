// Rollator test rig, BENCH VERSION: for testing with only the Arduino and servos (no ESP32).
// Arduino Mega 2560 or Uno R3, same pins. Same serial protocol as rig_uno.ino, so the website
// and rig_driver.py work unchanged. Differences from rig_uno.ino:
//   - Wheel direction and stop-point trims are set here, because calibration needs the ESP32.
//   - A short servo wiggle at power-up shows which deck servos are wired.
//   - The built-in LED shows state: off idle, on while running, blinking while returning,
//     3 fast flashes on any ERR.
// Go back to rig_uno.ino once the ESP32 is here and `rig_driver.py calibrate` has been run.
//
// Servos only:
//   D5  left wheel   continuous servo (FS90R type)
//   D6  right wheel  continuous servo
//   D9  deck yaw     standard servo (SG90): heading, 0-180 deg = -90..+90 deg
//   D10 deck pitch   standard servo: slopes, curb lift, and the crack/curb jolts
//   D11 deck roll    standard servo, optional: cross-slope
// No sensors on this board. Wheel speeds come from data/calibration.json on the laptop,
// measured once with the ESP32's photoresistor wheel counters (rig_driver.py calibrate).
//
// Receives a 50 Hz timeline from pipeline/rig_driver.py over USB serial (115200 baud).
// Protocol: see pipeline/rig/protocol.py.  Libraries: Servo (built in).

#include <Servo.h>

// ---------------------------------------------------------------- pins
const uint8_t PIN_WHEEL_L = 5;
const uint8_t PIN_WHEEL_R = 6;
const uint8_t PIN_YAW = 9;
const uint8_t PIN_PITCH = 10;
const uint8_t PIN_ROLL = 11;

#if defined(ARDUINO_AVR_MEGA2560)
const uint8_t QUEUE_SIZE = 32;
#else
const uint8_t QUEUE_SIZE = 24;
#endif

// ---------------------------------------------------------------- bench settings (edit these)
// The two wheel servos face opposite ways on the rig, so one of them usually needs inverting.
// Test with:  python rig_driver.py spin --port COM3 --left 1600 --right 1600
// Each wheel should roll the way the rig "walks". Flip the flag for any wheel that goes backwards.
const bool INVERT_LEFT = false;
const bool INVERT_RIGHT = false;
// If a wheel creeps when sent exactly 1500 us, nudge its trim (try steps of 5) until it stops:
//   python rig_driver.py spin --port COM3 --left 1500 --right 1500
// (or turn the small trim pot on the servo, if it has one, and leave these at 0).
const int16_t TRIM_LEFT_US = 0;
const int16_t TRIM_RIGHT_US = 0;
const bool BOOT_WIGGLE = true;     // set false to skip the power-up servo check
const bool HAS_ROLL_SERVO = false; // set true once a servo is on D11

// ---------------------------------------------------------------- tuning
const uint8_t SERVO_CENTRE = 90;
const int8_t JOLT_DIR = 1;          // flip to -1 if jolts tilt the deck the wrong way
// Deck trims, in servo degrees: added to every deck angle so the deck rests straight and level.
// Normally leave these at 0: set them live with `python rig_driver.py trim --port COMx`, which
// saves data/deck.json, and the laptop sends them (D command) every time it connects.
const int8_t YAW_TRIM_DEG = 0;
const int8_t PITCH_TRIM_DEG = 0;
const int8_t ROLL_TRIM_DEG = 0;
int8_t yawTrim = YAW_TRIM_DEG, pitchTrim = PITCH_TRIM_DEG, rollTrim = ROLL_TRIM_DEG;
const uint16_t JOLT_MS = 70;        // how long the deck stays flicked before snapping back
const uint16_t BUZZ_HALF_MS = 80;   // rough patch: about 6 flicks per second

// ---------------------------------------------------------------- hardware
Servo wheelL, wheelR, yawServo, pitchServo, rollServo;

// ---------------------------------------------------------------- command queue
struct Cmd {
  uint32_t t;      // ms since B
  uint16_t left;   // wheel pulse width in us, 0 = stop (detached)
  uint16_t right;
  uint8_t yaw;     // servo degrees
  uint8_t pitch;
  uint8_t roll;
  uint8_t jolt;    // 0 none, 1 small crack, 2 large crack, 3 curb drop, 4 sync
  uint8_t buzz;    // 0 off, 1 light, 2 heavy
};
Cmd queue[QUEUE_SIZE];
uint8_t qHead = 0, qCount = 0;
Cmd cur;

enum State : uint8_t { IDLE, RUNNING, RETURNING };
State state = IDLE;
bool ending = false, aborted = false, starvedReported = false;
uint32_t t0 = 0, executed = 0, lastReport = 0, returnUntil = 0;

// pitch = commanded angle + jolt/buzz offset
uint8_t pitchBase = SERVO_CENTRE;
uint32_t joltUntil = 0;
uint8_t joltAmp = 0;
uint8_t buzzLevel = 0;
uint32_t buzzNext = 0;
bool buzzUp = false;
int16_t lastPitchWritten = -1;

char line[72];
uint8_t lineLen = 0;

// ---------------------------------------------------------------- status LED
uint32_t errFlashUntil = 0;

void flagError() { errFlashUntil = millis() + 600; }

void updateLed(uint32_t now) {
  bool on;
  if ((int32_t)(now - errFlashUntil) < 0) on = (now / 100) % 2;   // 3 fast flashes
  else if (state == RUNNING) on = true;
  else if (state == RETURNING) on = (now / 250) % 2;
  else on = false;
  digitalWrite(LED_BUILTIN, on ? HIGH : LOW);
}

// ---------------------------------------------------------------- helpers
// The laptop sends pulse widths for an uncalibrated wheel (neutral 1500, forward = above 1500).
// Mirror and trim them here.
uint16_t benchPulse(uint16_t us, bool invert, int16_t trim) {
  if (us == 0) return 0;
  int32_t off = (int32_t)us - 1500;
  if (invert) off = -off;
  return (uint16_t)constrain(1500 + trim + off, 500, 2500);
}

void driveWheel(Servo &s, uint8_t pin, uint16_t us) {
  if (us != 0) {
    if (pin == PIN_WHEEL_L) us = benchPulse(us, INVERT_LEFT, TRIM_LEFT_US);
    else us = benchPulse(us, INVERT_RIGHT, TRIM_RIGHT_US);
  }
  if (us == 0) {
    if (s.attached()) s.detach();  // no pulses = no creep
  } else {
    if (!s.attached()) s.attach(pin);
    s.writeMicroseconds(us);
  }
}

void stopWheels() {
  driveWheel(wheelL, PIN_WHEEL_L, 0);
  driveWheel(wheelR, PIN_WHEEL_R, 0);
}

void writePitch(int angle) {
  angle = constrain(angle + pitchTrim, 0, 180);
  if (angle != lastPitchWritten) {
    pitchServo.write(angle);
    lastPitchWritten = angle;
  }
}

void writeYaw(int angle) { yawServo.write(constrain(angle + yawTrim, 0, 180)); }
void writeRoll(int angle) { rollServo.write(constrain(angle + rollTrim, 0, 180)); }

void centreDeck() {
  writeYaw(SERVO_CENTRE);
  writeRoll(SERVO_CENTRE);
  pitchBase = SERVO_CENTRE;
  buzzLevel = 0;
  joltUntil = 0;
  writePitch(SERVO_CENTRE);
}

void clearQueue() {
  qHead = 0;
  qCount = 0;
}

void beginReturn(bool wasAborted, uint32_t now) {
  aborted = wasAborted;
  stopWheels();
  clearQueue();
  centreDeck();
  returnUntil = now + 800;  // give the deck time to swing back to centre
  state = RETURNING;
}

uint8_t joltAmplitude(uint8_t code) {
  switch (code) {
    case 1: return 6;    // small crack
    case 2: return 12;   // large crack
    case 3: return 18;   // curb drop
    case 4: return 10;   // sync jolt
    default: return 0;
  }
}

void updatePitch(uint32_t now) {
  int offset = 0;
  if (joltUntil) {
    if ((int32_t)(now - joltUntil) < 0) offset = JOLT_DIR * joltAmp;
    else {
      joltUntil = 0;
      buzzNext = now + BUZZ_HALF_MS;
    }
  } else if (buzzLevel) {
    if ((int32_t)(now - buzzNext) >= 0) {
      buzzUp = !buzzUp;
      buzzNext = now + BUZZ_HALF_MS;
    }
    if (buzzUp) offset = JOLT_DIR * (buzzLevel == 1 ? 3 : 6);
  }
  writePitch(pitchBase + offset);
}

void apply(const Cmd &c, uint32_t now) {
  if (c.left != cur.left) driveWheel(wheelL, PIN_WHEEL_L, c.left);
  if (c.right != cur.right) driveWheel(wheelR, PIN_WHEEL_R, c.right);
  if (c.yaw != cur.yaw) writeYaw(c.yaw);
  if (c.roll != cur.roll) writeRoll(c.roll);
  pitchBase = c.pitch;
  buzzLevel = c.buzz;
  if (c.jolt) {
    joltAmp = joltAmplitude(c.jolt);
    joltUntil = now + JOLT_MS;
  }
  cur = c;
}

// Parses up to `n` comma-separated integers after the first comma. Returns how many it read.
uint8_t parseInts(char *s, long *v, uint8_t n) {
  char *p = strchr(s, ',');
  uint8_t i = 0;
  while (p && i < n) {
    p++;
    char *end;
    v[i] = strtol(p, &end, 10);
    if (end == p) break;
    i++;
    p = (*end == ',') ? end : nullptr;
  }
  return i;
}

bool parseT(char *s) {
  long v[8];
  if (parseInts(s, v, 8) != 8) return false;
  if (qCount >= QUEUE_SIZE) {
    Serial.println(F("ERR,queue overflow"));
    flagError();
    return true;
  }
  Cmd &c = queue[(qHead + qCount) % QUEUE_SIZE];
  c.t = (uint32_t)v[0];
  c.left = (uint16_t)constrain(v[1], 0, 2500);
  c.right = (uint16_t)constrain(v[2], 0, 2500);
  c.yaw = (uint8_t)constrain(v[3], 0, 180);
  c.pitch = (uint8_t)constrain(v[4], 0, 180);
  c.roll = (uint8_t)constrain(v[5], 0, 180);
  c.jolt = (uint8_t)v[6];
  c.buzz = (uint8_t)v[7];
  qCount++;
  return true;
}

void sendReady() {
  Serial.print(F("READY,"));
  Serial.println(QUEUE_SIZE);
}

void handleLine(char *s, uint32_t now) {
  long v[3];
  switch (s[0]) {
    case 'T':
      if (!parseT(s)) {
        Serial.println(F("ERR,bad T line"));
        flagError();
      }
      break;
    case 'B':
      t0 = now;
      executed = 0;
      lastReport = 0;
      ending = aborted = starvedReported = false;
      cur = Cmd{0, 0, 0, SERVO_CENTRE, SERVO_CENTRE, SERVO_CENTRE, 0, 0};
      state = RUNNING;
      break;
    case 'E':
      ending = true;
      break;
    case 'X':
      if (state == IDLE) Serial.println(F("DONE,aborted"));
      else beginReturn(true, now);
      break;
    case 'H':
      stopWheels();
      clearQueue();
      centreDeck();
      state = IDLE;
      sendReady();
      break;
    case 'D':  // deck trims: D,yaw,pitch,roll in degrees (idle only); re-centres the deck
      if (state == IDLE && parseInts(s, v, 3) == 3) {
        yawTrim = (int8_t)constrain(v[0], -90, 90);
        pitchTrim = (int8_t)constrain(v[1], -90, 90);
        rollTrim = (int8_t)constrain(v[2], -90, 90);
        lastPitchWritten = -1;
        centreDeck();
        Serial.println(F("OK"));
      } else {
        Serial.println(F("ERR,D only works while idle"));
        flagError();
      }
      break;
    case 'C':  // calibration: C,left_us,right_us drives the wheels directly (0 = stop)
      if (state == IDLE && parseInts(s, v, 2) == 2) {
        driveWheel(wheelL, PIN_WHEEL_L, (uint16_t)constrain(v[0], 0, 2500));
        driveWheel(wheelR, PIN_WHEEL_R, (uint16_t)constrain(v[1], 0, 2500));
        Serial.println(F("OK"));
      } else {
        Serial.println(F("ERR,C only works while idle"));
        flagError();
      }
      break;
    default:
      Serial.println(F("ERR,unknown command"));
      flagError();
  }
}

void readSerial(uint32_t now) {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      line[lineLen] = 0;
      if (lineLen) handleLine(line, now);
      lineLen = 0;
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = ch;
    }
  }
}

void report(uint32_t t) {
  Serial.print(F("P,"));
  Serial.print(t);
  Serial.print(',');
  Serial.print(executed);
  Serial.print(',');
  Serial.println(qCount);
}

// Moves yaw, then pitch, then roll 20 degrees each way and back (about 1.2 s in total),
// so you can see at power-up that each deck servo is wired to the right pin.
void bootWiggle() {
  Servo *deck[3] = {&yawServo, &pitchServo, &rollServo};
  uint8_t count = HAS_ROLL_SERVO ? 3 : 2;
  for (uint8_t i = 0; i < count; i++) {
    digitalWrite(LED_BUILTIN, HIGH);
    deck[i]->write(SERVO_CENTRE + 20);
    delay(200);
    digitalWrite(LED_BUILTIN, LOW);
    deck[i]->write(SERVO_CENTRE - 20);
    delay(200);
    deck[i]->write(SERVO_CENTRE);
    delay(150);
  }
  lastPitchWritten = -1;
  centreDeck();   // finish at the trimmed centre
}

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  yawServo.attach(PIN_YAW);
  pitchServo.attach(PIN_PITCH);
  if (HAS_ROLL_SERVO) rollServo.attach(PIN_ROLL);
  pinMode(LED_BUILTIN, OUTPUT);
  centreDeck();
  if (BOOT_WIGGLE) bootWiggle();
  stopWheels();  // wheel servos stay detached until a run moves them
  sendReady();
}

void loop() {
  uint32_t now = millis();
  readSerial(now);

  if (state == RUNNING) {
    uint32_t t = now - t0;
    while (qCount && queue[qHead].t <= t) {
      apply(queue[qHead], now);
      qHead = (qHead + 1) % QUEUE_SIZE;
      qCount--;
      executed++;
      starvedReported = false;
    }
    if (!qCount) {
      if (ending && t >= cur.t) {
        beginReturn(false, now);
      } else if (!ending && t > cur.t + 100) {
        stopWheels();  // laptop fell behind: hold still rather than run blind
        cur.left = cur.right = 0;
        if (!starvedReported) {
          Serial.println(F("ERR,starved"));
          flagError();
          starvedReported = true;
        }
      }
    }
  }

  updatePitch(now);

  // progress reports continue while returning, so the laptop knows the rig is alive
  if (state != IDLE && now - t0 - lastReport >= 50) {
    lastReport = now - t0;
    report(lastReport);
  }

  updateLed(now);

  if (state == RETURNING && (int32_t)(now - returnUntil) >= 0) {
    state = IDLE;
    Serial.println(aborted ? F("DONE,aborted") : F("DONE"));
  }
}
