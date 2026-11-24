// ESP32: photoresistor wheel counter for calibrating the rig's wheel servos.
//
// Each wheel has 4 holes. An LED on the back (inner side) of the wheel shines through the
// holes onto a photoresistor on the front (outer side). Every hole that passes = one count.
//
// Wiring (per wheel); the pins switch with the board picked in the IDE:
//   3V3 -> photoresistor -> sensor pin; 10k from that pin -> GND.
//     ESP32 Dev Module:    GPIO 34 (left) / GPIO 35 (right)
//     ESP32-C3 (SuperMini): GPIO 0 (left)  / GPIO 1 (right)
//     ESP32-S3:            GPIO 1 (left)  / GPIO 2 (right)
//   5 V (VIN / 5V pin) -> 220 ohm -> LED long leg; LED short leg -> GND. (Always on.)
//   Put the photoresistor in a ~2 cm cardboard/paper tube so room light can't reach it.
//
// Prints "H,<ms>,<left_count>,<right_count>,<left_edge_ms>,<right_edge_ms>" every 100 ms at
// 115200 baud; *_edge_ms is when that wheel's latest hole passed (calibration times holes).
// Used by: python rig_driver.py calibrate --port <Arduino port> --esp-port <ESP32 port>
// Board: ESP32 Dev Module, ESP32C3 Dev Module (SuperMini) or ESP32S3. No libraries needed.
// On a C3/S3 SuperMini, also set Tools > USB CDC On Boot > Enabled, or nothing prints over USB.
//
// The threshold adapts by itself: it counts a hole when the light rises past 60 % of the way
// from dark to light (and resets below 40 %). After every notch it re-learns "dark" and "light"
// from that notch's actual readings, so a stray extreme (a hand, the LED during setup) only
// matters until the first notch passes.
// Set SHOW_LEVELS = true to also print "L,<left>,<right>,<left_thr>,<right_thr>" lines
// while setting up (calibration ignores them).

#if defined(CONFIG_IDF_TARGET_ESP32C3)
const int PIN_L = 0;                // ADC1 pins: these still work alongside Wi-Fi later
const int PIN_R = 1;
#elif defined(CONFIG_IDF_TARGET_ESP32S3)
const int PIN_L = 1;
const int PIN_R = 2;
#else                               // classic ESP32 (Dev Module)
const int PIN_L = 34;
const int PIN_R = 35;
#endif
const bool SHOW_LEVELS = false;

const uint32_t SAMPLE_US = 2000;    // read both sensors 500 times a second
const uint32_t MIN_GAP_MS = 40;     // ignore holes closer than 40 ms (top speed is ~125 ms apart)
const int OVERSAMPLE = 16;          // each reading = average of 16, to cut the ESP32's ADC noise
const int MIN_RANGE = 40;           // need at least this much light/dark difference (of 4095);
                                    // a good beam gives hundreds: shade the photoresistor if not
const float DECAY = 0.002f;         // how fast old min/max readings fade (~1 count/s), so a slow
                                    // wheel keeps its range between notches

struct Wheel {
  int pin;
  float lo = 4095, hi = 0;          // dark / light levels the thresholds sit between
  float darkMin = 4095, lightMax = 0;  // extremes of the current dark / light phase
  bool lit = false;                 // currently looking through a hole
  uint32_t count = 0;
  uint32_t edgeMs = 0;              // time of the latest counted hole
  int level = 0;

  void sample(uint32_t now) {
    uint32_t sum = 0;
    for (int i = 0; i < OVERSAMPLE; i++) sum += analogRead(pin);
    level = sum / OVERSAMPLE;       // brighter = higher (photoresistor on the 3V3 side)
    if (level > hi) hi = level; else hi -= DECAY;
    if (level < lo) lo = level; else lo += DECAY;
    if (lit) { if (level > lightMax) lightMax = level; }
    else if (level < darkMin) darkMin = level;
    float range = hi - lo;
    if (range < MIN_RANGE) {        // no clear light/dark difference yet (or the wheel is still)
      return;
    }
    if (!lit && level > lo + 0.6f * range) {
      lit = true;
      lo = darkMin;                 // the dark phase that just ended is the new "dark"
      lightMax = level;
      if (now - edgeMs >= MIN_GAP_MS) {
        count++;
        edgeMs = now;
      }
    } else if (lit && level < lo + 0.4f * range) {
      lit = false;
      hi = lightMax;                // the light phase that just ended is the new "light"
      darkMin = level;
    }
  }

  int threshold() const { return (int)(lo + 0.6f * (hi - lo)); }
};

Wheel left, right;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  pinMode(PIN_L, INPUT);
  pinMode(PIN_R, INPUT);
  left.pin = PIN_L;
  right.pin = PIN_R;
}

void loop() {
  static uint32_t nextSample = 0, nextReport = 0;
  uint32_t us = micros();
  if ((int32_t)(us - nextSample) >= 0) {
    nextSample = us + SAMPLE_US;
    uint32_t now = millis();
    left.sample(now);
    right.sample(now);
  }

  uint32_t now = millis();
  // if ((int32_t)(now - nextReport) >= 0) {
  //   nextReport = now + 100;
  //   Serial.printf("H,%lu,%lu,%lu,%lu,%lu\n", (unsigned long)now, (unsigned long)left.count,
  //                 (unsigned long)right.count, (unsigned long)left.edgeMs, (unsigned long)right.edgeMs);
  //   if (SHOW_LEVELS)
  //     Serial.printf("L,%d,%d,%d,%d\n", left.level, right.level, left.threshold(), right.threshold());
  }
}
