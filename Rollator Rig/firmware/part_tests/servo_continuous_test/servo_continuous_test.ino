// PART TEST: the two continuous-rotation wheel servos. Do they spin both ways and stop?
// Arduino Mega or Uno. Open the Serial Monitor at 115200 baud ("Newline" line ending).
//
// Wiring: left wheel signal -> D5, right wheel signal -> D6.
//         Red/brown from the wheel battery (9V through 2 diodes in your setup).
//         Battery minus MUST also go to an Arduino GND pin, or the servos won't follow the signal.
//
// It runs this loop on both wheels at once, 2 seconds per step:
//   STOP (1500) -> slow forward (1600) -> fast forward (2000) -> STOP -> slow reverse (1400) -> fast reverse (1000)
// What to check:
//   - Both wheels spin in every step except STOP, faster at 2000/1000 than at 1600/1400.
//   - They reverse direction between the forward and reverse steps.
//   - At STOP they stand still. If one creeps, that's normal for cheap servos: turn its trim pot
//     (small hole on the case) until it stops, or note the pulse that stops it (see below).
//
// Type a number (500-2500) and press Enter to hold both wheels at that pulse width, e.g. 1500,
// 1510, 1490, to find each wheel's real stop point. Type "a" to go back to the automatic loop.

#include <Servo.h>

const int PIN_LEFT = 5;
const int PIN_RIGHT = 6;

Servo left, right;

const int STEPS[] = {1500, 1600, 2000, 1500, 1400, 1000};
const char *NAMES[] = {"STOP", "slow forward", "fast forward", "STOP", "slow reverse", "fast reverse"};
const int NUM_STEPS = 6;
const unsigned long STEP_MS = 2000;

bool manual = false;
int step = -1;
unsigned long nextStep = 0;

void setBoth(int us) {
  left.writeMicroseconds(us);
  right.writeMicroseconds(us);
}

void setup() {
  Serial.begin(115200);
  left.attach(PIN_LEFT);
  right.attach(PIN_RIGHT);
  setBoth(1500);
  Serial.println(F("Continuous servo test. Type a pulse width (e.g. 1500) or 'a' for auto."));
}

void loop() {
  if (Serial.available()) {
    String s = Serial.readStringUntil('\n');
    s.trim();
    if (s == "a") {
      manual = false;
      step = -1;
      nextStep = 0;
      Serial.println(F("Auto loop."));
    } else if (s.length()) {
      int us = constrain(s.toInt(), 500, 2500);
      manual = true;
      setBoth(us);
      Serial.print(F("Holding both wheels at "));
      Serial.print(us);
      Serial.println(F(" us. Type 'a' for auto."));
    }
  }

  if (!manual && millis() >= nextStep) {
    step = (step + 1) % NUM_STEPS;
    setBoth(STEPS[step]);
    Serial.print(STEPS[step]);
    Serial.print(F(" us  "));
    Serial.println(NAMES[step]);
    nextStep = millis() + STEP_MS;
  }
}
