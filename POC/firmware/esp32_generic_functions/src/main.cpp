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
//
// Pin allowlist is hard-coded here and is independent of and
// non-overridable by the agent/bridge -- this is the firmware safety layer.

#include <Arduino.h>

const int ALLOWED_DIGITAL_PINS[] = {5, 2};
const int ALLOWED_PWM_PINS[] = {5};
const int ALLOWED_ADC_PINS[] = {34};

const int PWM_FREQ = 5000;
const int PWM_RESOLUTION_BITS = 8; // duty range 0-255
const int PWM_CHANNEL = 0;         // only used on the legacy (pre-3.x) ledc API

bool isAllowed(int pin, const int *list, int len);
void handleCommand(const String &line);
void handleSetGpio(const String &rest);
void handleReadGpio(const String &rest);
void handleSetPwm(const String &rest);
void handleReadAdc(const String &rest);

bool isAllowed(int pin, const int *list, int len) {
  for (int i = 0; i < len; i++) {
    if (list[i] == pin) return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);

  for (unsigned int i = 0; i < sizeof(ALLOWED_DIGITAL_PINS) / sizeof(int); i++) {
    pinMode(ALLOWED_DIGITAL_PINS[i], OUTPUT);
  }

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(ALLOWED_PWM_PINS[0], PWM_FREQ, PWM_RESOLUTION_BITS);
#else
  ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_RESOLUTION_BITS);
  ledcAttachPin(ALLOWED_PWM_PINS[0], PWM_CHANNEL);
#endif

  Serial.println("READY");
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
  if (line == "PING") {
    Serial.println("OK PONG");
    return;
  }

  int firstSpace = line.indexOf(' ');
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
  digitalWrite(pin, value ? HIGH : LOW);
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
  ledcWrite(ALLOWED_PWM_PINS[0], duty);
#else
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
  int value = analogRead(pin);
  Serial.print("OK VALUE=");
  Serial.println(value);
}
