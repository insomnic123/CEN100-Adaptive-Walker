// PART TEST: MPU-9250 (GY-9250 board) on the ESP32. Is it wired right, and does it sense motion?
// Board: ESP32 Dev Module. Serial Monitor at 115200 baud. No libraries needed (Wire is built in).
//
// Wiring: VCC -> 3V3, GND -> GND, SCL -> D22, SDA -> D21, AD0 -> GND, NCS -> 3V3,
//         INT -> D19, FSYNC -> GND. Leave EDA and ECL unconnected.
//
// Quiet by default: type a letter + Enter in the Serial Monitor ("Newline" line ending):
//   i   chip info (address and ID)
//   r   one reading: acceleration (g), rotation (deg/s), and the tilt worked out from gravity
//   c   continuous readings, 5 a second; type c again to stop
// What to check:
//   - "found at 0x68" and an ID of 0x71 (MPU-9250), 0x73 (MPU-9255) or 0x70 (MPU-6500;
//     same motion sensors, no compass, which is fine for this project).
//   - Lying flat: az about +1.00 g, ax and ay near 0. Tilt it by hand: the angles follow.
//   - Turn it: gz moves away from 0 while turning and comes back when still.
//   - Run a course: pitch follows slopes, the jolts show as spikes in ax/az, yaw turns as gz.

#include <Wire.h>

const int PIN_SDA = 21, PIN_SCL = 22;
uint8_t addr = 0x68;

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(addr, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

void writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

bool present(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  if (present(0x68)) addr = 0x68;
  else if (present(0x69)) addr = 0x69;
  else {
    Serial.println(F("IMU not found on SDA D21 / SCL D22. Check VCC->3V3, GND, SDA, SCL, and NCS->3V3."));
    while (true) delay(1000);
  }
  writeReg(0x6B, 0x00);         // wake up
  delay(100);
  writeReg(0x1B, 0x08);         // gyro +-500 deg/s
  writeReg(0x1C, 0x00);         // accel +-2 g
  printInfo();
  Serial.println(F("Type i (chip info), r (one reading) or c (continuous on/off), then Enter."));
}

void printInfo() {
  Serial.printf("IMU found at 0x%02X%s\n", addr, addr == 0x69 ? "  (AD0 isn't on GND: fine for this test)" : "");
  uint8_t id = readReg(0x75);   // WHO_AM_I
  Serial.printf("chip ID 0x%02X (%s)\n", id,
                id == 0x71 ? "MPU-9250" : id == 0x73 ? "MPU-9255" : id == 0x70 ? "MPU-6500, no compass" : "unexpected");
}

void printReading() {
  Wire.beginTransmission(addr);
  Wire.write(0x3B);             // ACCEL_XOUT_H: accel x,y,z, temperature, gyro x,y,z
  Wire.endTransmission(false);
  Wire.requestFrom(addr, (uint8_t)14);
  int16_t raw[7];
  for (int i = 0; i < 7; i++) raw[i] = (Wire.read() << 8) | Wire.read();
  float ax = raw[0] / 16384.0f, ay = raw[1] / 16384.0f, az = raw[2] / 16384.0f;
  float gx = raw[4] / 65.5f, gy = raw[5] / 65.5f, gz = raw[6] / 65.5f;
  float pitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * 180 / PI;
  float roll = atan2f(ay, az) * 180 / PI;
  Serial.printf("accel g  x %+5.2f  y %+5.2f  z %+5.2f   |  gyro deg/s  x %+7.1f  y %+7.1f  z %+7.1f"
                "   |  tilt: pitch %+5.1f  roll %+5.1f\n", ax, ay, az, gx, gy, gz, pitch, roll);
}

bool continuous = false;

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'i') printInfo();
    else if (c == 'r') printReading();
    else if (c == 'c') {
      continuous = !continuous;
      Serial.println(continuous ? F("continuous on (type c to stop)") : F("continuous off"));
    }
  }
  static unsigned long next = 0;
  if (continuous && millis() >= next) {
    next = millis() + 200;
    printReading();
  }
}
