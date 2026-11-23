// PART TEST: A3144 Hall sensors. Does each sensor see a magnet?
// Works on an Arduino (Mega/Uno) or the ESP32: pick the board in the IDE, the pins switch.
// Open the Serial Monitor at 115200 baud and wave a magnet past each sensor.
//
// The A3144 only reacts to ONE face of the magnet (south pole facing the printed side).
// If nothing happens, flip the magnet over before blaming the sensor.
//
// Wiring (A3144, flat printed side facing you, legs down: left = VCC, middle = GND, right = OUT)
//   Arduino:  VCC -> 5V,  GND -> GND,  OUT -> D2 (sensor A) / D3 (sensor B). No resistor needed.
//   ESP32:    VCC -> VIN, GND -> GND,  OUT -> GPIO 34 (A) / GPIO 35 (B),
//             plus a 10k resistor from each OUT to 3V3 (these pins have no built-in pull-up).

#if defined(ESP32)
const int PIN_A = 34;
const int PIN_B = 35;
const int PIN_LED = 2;          // blue LED on most ESP32 dev boards
const int INPUT_MODE = INPUT;   // external 10k pull-ups do the job
#else
const int PIN_A = 2;
const int PIN_B = 3;
const int PIN_LED = LED_BUILTIN;
const int INPUT_MODE = INPUT_PULLUP;
#endif

int lastA = HIGH, lastB = HIGH;
unsigned long countA = 0, countB = 0;

void setup() {
  Serial.begin(115200);
  pinMode(PIN_A, INPUT_MODE);
  pinMode(PIN_B, INPUT_MODE);
  pinMode(PIN_LED, OUTPUT);
  Serial.println(F("Hall test ready. Bring a magnet close to each sensor."));
  Serial.println(F("Expect: 'magnet' when close, 'clear' when moved away, LED on while any magnet is near."));
}

void loop() {
  int a = digitalRead(PIN_A);   // LOW = magnet detected
  int b = digitalRead(PIN_B);

  if (a != lastA) {
    if (a == LOW) countA++;
    Serial.print(F("Sensor A: "));
    Serial.print(a == LOW ? F("magnet") : F("clear"));
    Serial.print(F("   (passes so far: "));
    Serial.print(countA);
    Serial.println(F(")"));
    lastA = a;
  }
  if (b != lastB) {
    if (b == LOW) countB++;
    Serial.print(F("Sensor B: "));
    Serial.print(b == LOW ? F("magnet") : F("clear"));
    Serial.print(F("   (passes so far: "));
    Serial.print(countB);
    Serial.println(F(")"));
    lastB = b;
  }

  digitalWrite(PIN_LED, (a == LOW || b == LOW) ? HIGH : LOW);
  delay(2);  // tiny debounce
}
