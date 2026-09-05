// Generic, Firmata-inspired pin-level primitive firmware.
// Exposes fixed commands over serial; never anything domain-named --
// SET_GPIO/READ_GPIO/SET_PWM/READ_ADC work on any allowed pin, so adding a
// new simple digital/analog device later means new Python in
// bridge/tool_functions.py, not new firmware.
//
// Protocol: one command per line in, one response line out.
//   PING                  -> OK PONG
//   SET_GPIO <pin> <val>  -> OK  | ERR <reason>
//   READ_GPIO <pin>       -> OK VALUE=<0|1>  | ERR <reason>
//   SET_PWM <pin> <duty>  -> OK  | ERR <reason>   (duty 0-255)
//   READ_ADC <pin>        -> OK VALUE=<0-4095>  | ERR <reason>
//   READ_PWM <pin>        -> OK VALUE=<0-255>  | ERR <reason>
//
// Pin allowlist is hard-coded here and is independent of and
// non-overridable by the agent/bridge -- this is the firmware safety layer.

#include <Arduino.h>


//Firmware safety layer: allowed list of pins that can be interacted with
const int ALLOWED_DIGITAL_PINS[] = {5, 2};
const int ALLOWED_PWM_PINS[] = {5};
const int ALLOWED_ADC_PINS[] = {34};

// PWM Constraints
const int PWM_FREQ = 5000;
const int PWM_RESOLUTION_BITS = 8; // duty range 0-255 (0- off : 255- full on)
const int PWM_CHANNEL = 0;         // only used on the legacy (pre-3.x) ledc API

bool isAllowed(int pin, const int *list, int len);
void handleCommand(const String &line);
void handleSetGpio(const String &rest);
void handleReadGpio(const String &rest);
void handleSetPwm(const String &rest);
void handleReadAdc(const String &rest);
void handleReadPwm(const String &rest);


// Firmware safety layer: only allow commands on pins in the allowlist
bool isAllowed(int pin, const int *list, int len) {
  // return true;  Temporary override for testing; remove this line to enforce allowlist
  for (int i = 0; i < len; i++) {
    if (list[i] == pin) return true;
  }
  return false;
}


void setup() {
  Serial.begin(115200);

  // Initialize all ALLOWED_DIGITAL_PINS to be writen to not read
  for (unsigned int i = 0; i < sizeof(ALLOWED_DIGITAL_PINS) / sizeof(int); i++) {
    pinMode(ALLOWED_DIGITAL_PINS[i], OUTPUT);
  }

  // PWM setup: Bind to pin and configure resolution and frequency.
#if ESP_ARDUINO_VERSION_MAJOR >= 3 //The ledc API is available in ESP32 v3.x only
  ledcAttach(ALLOWED_PWM_PINS[0], PWM_FREQ, PWM_RESOLUTION_BITS);
#else
  ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_RESOLUTION_BITS);
  ledcAttachPin(ALLOWED_PWM_PINS[0], PWM_CHANNEL);
#endif

  Serial.println("ESP32_READY");
}


void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      handleCommand(line);
    }
  }
}


void handleCommand(const String &line) {
  // Alive check cmnd
  if (line == "PING") {
    Serial.println("OK PONG");
    return;
  }


  int firstSpace = line.indexOf(' '); //-1 means no space found
  String cmd = firstSpace == -1 ? line : line.substring(0, firstSpace);
  String rest = firstSpace == -1 ? "" : line.substring(firstSpace + 1);

  if (cmd == "SET_GPIO") {
    handleSetGpio(rest);
  } else if (cmd == "READ_GPIO") {
    handleReadGpio(rest);
  } else if (cmd == "SET_PWM") {
    handleSetPwm(rest);
  } else if (cmd == "READ_ADC") {
    handleReadAdc(rest);
  } else if (cmd == "READ_PWM") {
    handleReadPwm(rest);
  } else {
    Serial.println("ERR UNKNOWN_COMMAND");
  }
}

void handleSetGpio(const String &rest) {
  int sp = rest.indexOf(' ');
  if (sp == -1) { Serial.println("ERR BAD_ARGS"); return; }
  int pin = rest.substring(0, sp).toInt();
  int value = rest.substring(sp + 1).toInt();
  if (!isAllowed(pin, ALLOWED_DIGITAL_PINS, sizeof(ALLOWED_DIGITAL_PINS) / sizeof(int))) {
    Serial.println("ERR PIN_NOT_ALLOWED");
    return;
  }
    // Release the pin from the LEDC (PWM) peripheral before driving it as a
  // plain digital output — otherwise LEDC silently keeps control of the
  // pin and digitalWrite() has no visible effect, even though it reports
  // success.
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcDetach(pin);
#else
  ledcDetachPin(pin);
#endif

  pinMode(pin, OUTPUT);

  digitalWrite(pin, value ? HIGH : LOW); // Drive the Pin HIGH for any non-zero value, LOW for 0
  Serial.println("OK");
}

void handleReadGpio(const String &rest) {
  int pin = rest.toInt();
  if (!isAllowed(pin, ALLOWED_DIGITAL_PINS, sizeof(ALLOWED_DIGITAL_PINS) / sizeof(int))) {
    Serial.println("ERR PIN_NOT_ALLOWED");
    return;
  }
  int value = digitalRead(pin);
  Serial.print("OK VALUE=");
  Serial.println(value);
}

void handleSetPwm(const String &rest) {
  int sp = rest.indexOf(' ');
  if (sp == -1) { Serial.println("ERR BAD_ARGS"); return; }
  int pin = rest.substring(0, sp).toInt();
  int duty = rest.substring(sp + 1).toInt();
  if (!isAllowed(pin, ALLOWED_PWM_PINS, sizeof(ALLOWED_PWM_PINS) / sizeof(int))) {
    Serial.println("ERR PIN_NOT_ALLOWED");
    return;
  }
  duty = constrain(duty, 0, 255);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  // handleSetGpio() may have ledcDetach()'d this pin to drive it as a plain
  // digital output (e.g. Stage 2/3's set_gpio calls before a later set_pwm
  // call on the same pin) -- re-attach here so SET_PWM keeps working
  // reliably no matter what was called on this pin before. Safe to call
  // again on an already-attached pin.
  ledcAttach(pin, PWM_FREQ, PWM_RESOLUTION_BITS);
  ledcWrite(pin, duty);
#else
  ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_RESOLUTION_BITS);
  ledcAttachPin(pin, PWM_CHANNEL);
  ledcWrite(PWM_CHANNEL, duty);
#endif
  Serial.println("OK");
}

void handleReadAdc(const String &rest) {
  int pin = rest.toInt();
  if (!isAllowed(pin, ALLOWED_ADC_PINS, sizeof(ALLOWED_ADC_PINS) / sizeof(int))) {
    Serial.println("ERR PIN_NOT_ALLOWED");
    return;
  }
  int value = analogRead(pin); // Read the ADC value (0-4095 for 12-bit ADC). Used here for the light sensor
  Serial.print("OK VALUE=");
  Serial.println(value);
}

void handleReadPwm(const String &rest) {
  int pin = rest.toInt();
  if (!isAllowed(pin, ALLOWED_PWM_PINS, sizeof(ALLOWED_PWM_PINS) / sizeof(int))) {
    Serial.println("ERR PIN_NOT_ALLOWED");
    return;
  }
  // Reads the *actual* current LEDC duty cycle from the peripheral itself --
  // not a value remembered on the Python side -- so it stays correct even
  // across bridge/dashboard restarts, which don't reset the ESP32.
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  int value = ledcRead(pin);
#else
  int value = ledcRead(PWM_CHANNEL);
#endif
  Serial.print("OK VALUE=");
  Serial.println(value);
}
