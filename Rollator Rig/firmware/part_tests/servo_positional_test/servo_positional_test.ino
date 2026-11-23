// PART TEST: the standard (positional) SG90 servos. Does each one move and hold an angle?
// Arduino Mega or Uno. Open the Serial Monitor at 115200 baud ("Newline" line ending).
//
// Wiring: yaw signal -> D9, pitch signal -> D10, roll signal -> D11 (optional).
//         Red -> Arduino 5V, brown -> Arduino GND (your setup; put the capacitor across 5V/GND).
//
// Test the servos BEFORE mounting them on the cardboard deck: the sweep uses the full 0-180 range.
//
// It tests one servo at a time (gentler on the Arduino's 5V supply):
//   centre (90) -> 0 -> 180 -> slow sweep 0..180..0 -> back to 90, then moves on to the next servo.
// What to check:
//   - Each servo moves smoothly to both ends and comes back to the same centre each time.
//   - No grinding, no buzzing that won't stop, no twitching while it should be still.
//   - The Arduino doesn't restart (if it does, the "Servo test ready" line prints again:
//     the 5V supply is sagging, so try one servo plugged in at a time).
//
// Type "yaw 45", "pitch 120" or "roll 90" and press Enter to send one servo to an angle.
// Type "a" to go back to the automatic test.

#include <Servo.h>

const int NUM = 3;
const int PINS[NUM] = {9, 10, 11};
const char *NAMES[NUM] = {"yaw (D9)", "pitch (D10)", "roll (D11)"};
Servo servos[NUM];

bool manual = false;

// Moves a servo and waits, but stops early if something is typed.
bool moveAndWait(Servo &s, int angle, unsigned long ms) {
  s.write(angle);
  unsigned long until = millis() + ms;
  while (millis() < until) {
    if (Serial.available()) return false;
  }
  return true;
}

bool testOne(int i) {
  Serial.print(F("Testing "));
  Serial.println(NAMES[i]);
  Servo &s = servos[i];
  Serial.println(F("  centre 90"));
  if (!moveAndWait(s, 90, 1000)) return false;
  Serial.println(F("  0"));
  if (!moveAndWait(s, 0, 1000)) return false;
  Serial.println(F("  180"));
  if (!moveAndWait(s, 180, 1000)) return false;
  Serial.println(F("  slow sweep"));
  for (int a = 180; a >= 0; a -= 2)
    if (!moveAndWait(s, a, 15)) return false;
  for (int a = 0; a <= 180; a += 2)
    if (!moveAndWait(s, a, 15)) return false;
  Serial.println(F("  back to centre 90"));
  return moveAndWait(s, 90, 1500);
}

void handleCommand() {
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd == "a") {
    manual = false;
    Serial.println(F("Auto test."));
    return;
  }
  int sp = cmd.indexOf(' ');
  if (sp < 0) {
    Serial.println(F("Try: yaw 45, pitch 120, roll 90, or a"));
    return;
  }
  String name = cmd.substring(0, sp);
  int angle = constrain(cmd.substring(sp + 1).toInt(), 0, 180);
  int i = name == "yaw" ? 0 : name == "pitch" ? 1 : name == "roll" ? 2 : -1;
  if (i < 0) {
    Serial.println(F("Unknown servo. Use yaw, pitch or roll."));
    return;
  }
  manual = true;
  servos[i].write(angle);
  Serial.print(NAMES[i]);
  Serial.print(F(" -> "));
  Serial.println(angle);
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < NUM; i++) {
    servos[i].attach(PINS[i]);
    servos[i].write(90);
    delay(300);  // one at a time, so they don't all jump at once
  }
  Serial.println(F("Servo test ready. Type e.g. 'yaw 45' to take over, 'a' for auto."));
}

void loop() {
  if (Serial.available()) {
    handleCommand();
    return;
  }
  if (manual) return;
  for (int i = 0; i < NUM && !manual; i++) {
    if (!testOne(i)) return;  // something was typed: handle it on the next loop
    servos[i].write(90);
  }
  Serial.println(F("All done. Starting over in 2 s (type something to take over)."));
  unsigned long until = millis() + 2000;
  while (millis() < until && !Serial.available()) {}
}
