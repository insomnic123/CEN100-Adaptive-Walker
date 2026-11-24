// ESP32 sensor kit (bench version, USB): IMU + wheel counters + button, streamed to the laptop.
//
// Board: ESP32 Dev Module. No libraries needed. (Wheel calibration still uses photo_counter_esp32.) Streams over USB serial at 921600 baud;
// rig_driver.py (run / watch with --esp-port) records it next to each run's answer key.
// (Wi-Fi/UDP comes later; the lines stay the same.)
//
// Wiring
//   MPU-6500/9250 (GY board): VCC -> 3V3, GND -> GND, SCL -> D22, SDA -> D21, AD0 -> GND,
//                             NCS -> 3V3, FSYNC -> GND, INT -> D19 (not used yet; polled instead)
//   Wheel photoresistors:     3V3 -> photoresistor -> D34 (left) / D35 (right), 10k from pin to GND
//   Wheel LEDs:               VIN -> 220 ohm -> LED -> GND
//   Button (optional):        D18 -> button -> GND
//
// Lines sent (all times are this board's millis()):
//   S,sensor_esp32,<imu_id_hex>                       once at start, and every 5 s
//   I,<ms>,<ax>,<ay>,<az>,<gx>,<gy>,<gz>              200 per second, raw: accel 4096 = 1 g (+-8 g),
//                                                      gyro 32.8 = 1 deg/s (+-1000 deg/s)
//   W,<ms>,<left>,<right>,<left_edge_ms>,<right_edge_ms>   wheel notch counts, 10 per second
//   K,<ms>,<1|0>                                      button pressed / released
//   E,<text>                                          a problem (e.g. IMU not found)

#include <Wire.h>

const int PIN_SDA = 21, PIN_SCL = 22;
const int PIN_WHEEL_L = 34, PIN_WHEEL_R = 35;
const int PIN_BUTTON = 18;
const uint32_t BAUD = 921600;
const uint32_t IMU_PERIOD_US = 5000;     // 200 Hz
const uint32_t WHEEL_PERIOD_US = 4000;   // 250 Hz (a notch takes 100+ ms to pass)
const uint32_t REPORT_MS = 100;

// ---------------------------------------------------------------- IMU (MPU-6500 / MPU-9250)
uint8_t imuAddr = 0x68;
uint8_t imuId = 0;
bool imuOk = false;

void imuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(imuAddr);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

uint8_t imuRead(uint8_t reg) {
  Wire.beginTransmission(imuAddr);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(imuAddr, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

bool imuPresent(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

void imuSetup() {
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  if (imuPresent(0x68)) imuAddr = 0x68;
  else if (imuPresent(0x69)) imuAddr = 0x69;
  else {
    Serial.println("E,IMU not found on SDA D21 / SCL D22");
    return;
  }
  imuWrite(0x6B, 0x80);                  // reset
  delay(100);
  imuWrite(0x6B, 0x01);                  // wake, best clock
  delay(50);
  imuWrite(0x1A, 0x03);                  // gyro low-pass ~41 Hz
  imuWrite(0x19, 0x04);                  // 1 kHz / 5 = 200 Hz internal rate
  imuWrite(0x1B, 0x10);                  // gyro +-1000 deg/s (jolts are fast)
  imuWrite(0x1C, 0x10);                  // accel +-8 g
  imuWrite(0x1D, 0x03);                  // accel low-pass ~41 Hz
  imuId = imuRead(0x75);
  imuOk = true;
}

void imuSample(uint32_t ms) {
  Wire.beginTransmission(imuAddr);
  Wire.write(0x3B);                      // accel x,y,z, temperature, gyro x,y,z
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(imuAddr, (uint8_t)14) != 14) return;
  int16_t v[7];
  for (int i = 0; i < 7; i++) v[i] = (int16_t)((Wire.read() << 8) | Wire.read());
  Serial.printf("I,%lu,%d,%d,%d,%d,%d,%d\n", (unsigned long)ms, v[0], v[1], v[2], v[4], v[5], v[6]);
}

// ---------------------------------------------------------------- wheel counters (photoresistors)
// Same logic as photo_counter_esp32: a notch counts when the light rises past 60 % of the way
// from dark to light, and dark/light are re-learned from every notch.
const int OVERSAMPLE = 8;
const int MIN_RANGE = 40;
const float DECAY = 0.002f;
const uint32_t MIN_GAP_MS = 40;

struct Wheel {
  int pin;
  float lo = 4095, hi = 0, darkMin = 4095, lightMax = 0;
  bool lit = false;
  uint32_t count = 0, edgeMs = 0;

  void sample(uint32_t now) {
    uint32_t sum = 0;
    for (int i = 0; i < OVERSAMPLE; i++) sum += analogRead(pin);
    float level = sum / (float)OVERSAMPLE;
    if (level > hi) hi = level; else hi -= DECAY;
    if (level < lo) lo = level; else lo += DECAY;
    if (lit) { if (level > lightMax) lightMax = level; }
    else if (level < darkMin) darkMin = level;
    float range = hi - lo;
    if (range < MIN_RANGE) return;
    if (!lit && level > lo + 0.6f * range) {
      lit = true;
      lo = darkMin;
      lightMax = level;
      if (now - edgeMs >= MIN_GAP_MS) { count++; edgeMs = now; }
    } else if (lit && level < lo + 0.4f * range) {
      lit = false;
      hi = lightMax;
      darkMin = level;
    }
  }
};
Wheel left, right;

// ---------------------------------------------------------------- button
bool buttonDown = false;
uint32_t buttonChangedMs = 0;

void buttonSample(uint32_t ms) {
  bool down = digitalRead(PIN_BUTTON) == LOW;
  if (down != buttonDown && ms - buttonChangedMs > 30) {   // 30 ms debounce
    buttonDown = down;
    buttonChangedMs = ms;
    Serial.printf("K,%lu,%d\n", (unsigned long)ms, down ? 1 : 0);
  }
}

// ---------------------------------------------------------------- setup / loop
void sendStatus() {
  Serial.printf("S,sensor_esp32,%02X\n", imuOk ? imuId : 0);
  if (!imuOk) Serial.println("E,IMU not found on SDA D21 / SCL D22");
}

void setup() {
  Serial.setTxBufferSize(8192);
  Serial.begin(BAUD);
  analogReadResolution(12);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  left.pin = PIN_WHEEL_L;
  right.pin = PIN_WHEEL_R;
  imuSetup();
  sendStatus();
}

void loop() {
  static uint32_t nextImu = micros(), nextWheel = micros(), nextReport = 0, nextStatus = 5000;
  uint32_t us = micros();
  uint32_t ms = millis();

  if (imuOk && (int32_t)(us - nextImu) >= 0) {
    nextImu += IMU_PERIOD_US;
    if ((int32_t)(us - nextImu) > 50000) nextImu = us + IMU_PERIOD_US;   // fell far behind: resync
    imuSample(ms);
  }
  if ((int32_t)(us - nextWheel) >= 0) {
    nextWheel = us + WHEEL_PERIOD_US;
    left.sample(ms);
    right.sample(ms);
    buttonSample(ms);
  }
  if ((int32_t)(ms - nextReport) >= 0) {
    nextReport = ms + REPORT_MS;
    Serial.printf("W,%lu,%lu,%lu,%lu,%lu\n", (unsigned long)ms, (unsigned long)left.count,
                  (unsigned long)right.count, (unsigned long)left.edgeMs, (unsigned long)right.edgeMs);
  }
  if ((int32_t)(ms - nextStatus) >= 0) {
    nextStatus = ms + 5000;
    sendStatus();
  }
}
