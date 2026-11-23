// ESP32: Hall-pulse counter for calibrating the rig's wheel servos.
//
// Wiring is the sensor kit's: A3144 OUT on GPIO 34 (left wheel)
// and GPIO 35 (right wheel), each with a 10 k pull-up to 3V3. 8 magnets per wheel.
// Prints "H,<ms>,<left_count>,<right_count>" every 100 ms at 115200 baud over USB.
// Used by: python rig_driver.py calibrate --port <Arduino port> --esp-port <ESP32 port>
// Board: any ESP32 Dev Module. No libraries needed.

const int PIN_HALL_L = 34;
const int PIN_HALL_R = 35;
const uint32_t MIN_GAP_US = 5000;  // ignore pulses closer than 5 ms (noise)

volatile uint32_t countL = 0, countR = 0;
volatile uint32_t lastL = 0, lastR = 0;

void IRAM_ATTR onHallL() {
  uint32_t now = micros();
  if (now - lastL >= MIN_GAP_US) {
    countL++;
    lastL = now;
  }
}

void IRAM_ATTR onHallR() {
  uint32_t now = micros();
  if (now - lastR >= MIN_GAP_US) {
    countR++;
    lastR = now;
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_HALL_L, INPUT);  // GPIO 34/35 have no internal pull-ups: the 10 k resistors do it
  pinMode(PIN_HALL_R, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_HALL_L), onHallL, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_HALL_R), onHallR, FALLING);
}

void loop() {
  static uint32_t next = 0;
  uint32_t now = millis();
  if (now >= next) {
    noInterrupts();
    uint32_t l = countL, r = countR;
    interrupts();
    Serial.printf("H,%lu,%lu,%lu\n", (unsigned long)now, (unsigned long)l, (unsigned long)r);
    next = now + 100;
  }
}
