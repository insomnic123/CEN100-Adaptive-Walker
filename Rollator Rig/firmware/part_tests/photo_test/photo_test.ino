// PART TEST: photoresistor + LED wheel sensors. Does each beam see the notches?
// Works on an Arduino (Mega/Uno) or the ESP32: pick the board in the IDE, the pins switch.
// Open the Serial Monitor at 115200 baud ("Newline" line ending).
//
// Prints a line only when a sensor changes between dark and LIGHT (a notch arriving/leaving),
// with the light level at that moment. Turning a wheel by hand should give 4 LIGHT lines per turn.
// Type  l  + Enter to print the current light levels, r + Enter to reset.
//
// Wiring (per sensor):
//   Arduino:  5V  -> photoresistor -> A0 (left) / A1 (right); 10k from that pin -> GND.
//   ESP32:    3V3 -> photoresistor -> GPIO 34 (left) / GPIO 35 (right); 10k from that pin -> GND.
//   LED:      5V (ESP32: VIN) -> 220 ohm -> LED long leg; short leg -> GND.
//
// Nothing prints while turning the wheel? Type l a few times, once in a notch and once blocked:
//   - both numbers almost the same: the beam isn't reaching the photoresistor (LED off, aim,
//     or the beam sits outside the notch depth), or room light swamps it (add a paper tube).
//   - one sensor never changes at all: check its wiring and pin.

// Each reading is an average of 16, which cuts noise. MIN_RANGE is the smallest light/dark
// difference treated as real; a well-shaded beam gives far more (hundreds on the ESP32).
#if defined(ESP32)
const int PIN_L = 34;
const int PIN_R = 35;
const int MIN_RANGE = 40;       // of 4095
#else
const int PIN_L = A0;
const int PIN_R = A1;
const int MIN_RANGE = 4;        // of 1023
#endif
const int OVERSAMPLE = 16;

int readLevel(int pin) {
  long sum = 0;
  for (int i = 0; i < OVERSAMPLE; i++) sum += analogRead(pin);
  return sum / OVERSAMPLE;
}

struct Sensor {
  int pin;
  const char *name;
  int lo, hi;                   // darkest / brightest seen since reset
  bool lit, ready;
  unsigned long holes;
  Sensor(int p, const char *n) : pin(p), name(n), lo(32767), hi(-1), lit(false), ready(false), holes(0) {}

  void reset() { lo = 32767; hi = -1; lit = false; ready = false; holes = 0; }

  void report(const __FlashStringHelper *what, int v) {
    Serial.print(name);
    Serial.print(F(": "));
    Serial.print(what);
    Serial.print(F("  level "));
    Serial.print(v);
    Serial.print(F("  (dark "));
    Serial.print(lo);
    Serial.print(F(", light "));
    Serial.print(hi);
    Serial.print(F(")  notches "));
    Serial.println(holes);
  }

  void update() {
    int v = readLevel(pin);     // brighter = higher number
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    int range = hi - lo;
    if (range < MIN_RANGE) return;
    if (!ready) {               // first time there's a clear light/dark difference
      ready = true;
      lit = v > lo + range / 2;
      report(lit ? F("sees light/dark now, starting LIGHT") : F("sees light/dark now, starting dark"), v);
      return;
    }
    if (!lit && v > lo + range * 6 / 10) {
      lit = true;
      holes++;
      report(F("LIGHT"), v);
    } else if (lit && v < lo + range * 4 / 10) {
      lit = false;
      report(F("dark "), v);
    }
  }
};

Sensor sensors[2] = {Sensor(PIN_L, "left"), Sensor(PIN_R, "right")};

void setup() {
  Serial.begin(115200);
#if defined(ESP32)
  analogReadResolution(12);
#endif
  Serial.println(F("Photoresistor test: turn a wheel slowly by hand. Lines print only on changes."));
  Serial.println(F("Type l + Enter for current levels, r + Enter to reset."));
}

void loop() {
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'r') {
      for (Sensor &s : sensors) s.reset();
      Serial.println(F("reset"));
    } else if (c == 'l') {
      for (Sensor &s : sensors) s.report(F("level now"), readLevel(s.pin));
    }
  }
  for (Sensor &s : sensors) s.update();
  delay(2);
}
