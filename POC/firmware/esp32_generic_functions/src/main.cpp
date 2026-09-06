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
//
// Architecture B (asynchronous MQTT telemetry) is compiled in ONLY when
// ENABLE_MQTT_TELEMETRY is defined -- see env:esp32doit-devkit-v1-mqtt in
// platformio.ini. The default env builds none of it, so the synchronous
// architecture above carries no WiFi/MQTT code and no broker dependency.
// That build adds three further commands:
//   TELEMETRY_START   -> OK  | ERR <reason>
//   TELEMETRY_STOP    -> OK
//   TELEMETRY_STATUS  -> OK STATE=<...> WIFI=<0|1> MQTT=<0|1> IP=<addr> ...

#include <Arduino.h>

#ifdef ENABLE_MQTT_TELEMETRY
#include <WiFi.h>
#include <PubSubClient.h>
#include "telemetry_config.h"
#endif


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

#ifdef ENABLE_MQTT_TELEMETRY
// --- Architecture B: asynchronous telemetry ------------------------------
// The MCU samples the ADC and publishes on its own schedule, so observations
// exist without the host ever issuing a READ_ADC.

enum TelemetryState {
  TELEM_OFF,        // disabled, or not yet started
  TELEM_WIFI_WAIT,  // WiFi association in progress
  TELEM_MQTT_WAIT,  // WiFi up, broker connection pending/retrying
  TELEM_STREAMING   // publishing
};

static WiFiClient telemetryWifiClient;
static PubSubClient mqttClient(telemetryWifiClient);

static TelemetryState telemetryState = TELEM_OFF;
static bool telemetryEnabled = TELEMETRY_AUTOSTART;
static bool telemetryPinAllowed = false;
static unsigned long lastPublishMs = 0;
static unsigned long lastAttemptMs = 0;
static bool firstAttempt = true;
static unsigned long publishedCount = 0;
static unsigned long failedPublishCount = 0;
static char telemetryTopic[64];

const unsigned long WIFI_RETRY_MS = 10000;
const unsigned long MQTT_RETRY_MS = 5000;

void telemetryService();
void publishTelemetrySample(unsigned long now);
void handleTelemetryStart();
void handleTelemetryStop();
void handleTelemetryStatus();
#endif


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

#ifdef ENABLE_MQTT_TELEMETRY
  // Telemetry obeys the same allowlist as every serial command -- the safety
  // layer is not bypassed just because the MCU initiated the read itself.
  telemetryPinAllowed = isAllowed(TELEMETRY_ADC_PIN, ALLOWED_ADC_PINS,
                                  sizeof(ALLOWED_ADC_PINS) / sizeof(int));
  snprintf(telemetryTopic, sizeof(telemetryTopic),
           "hardware/telemetry/adc/%d", TELEMETRY_ADC_PIN);
  mqttClient.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
  // connect() is the one blocking call in the telemetry path; a 1s socket
  // timeout bounds how long an unreachable broker can stall serial handling.
  mqttClient.setSocketTimeout(1);
  if (!telemetryPinAllowed) {
    telemetryEnabled = false;
  }
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

#ifdef ENABLE_MQTT_TELEMETRY
  // Non-blocking: one state transition or at most one publish per pass, so
  // serial commands stay responsive while telemetry streams.
  telemetryService();
#endif
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
#ifdef ENABLE_MQTT_TELEMETRY
  } else if (cmd == "TELEMETRY_START") {
    handleTelemetryStart();
  } else if (cmd == "TELEMETRY_STOP") {
    handleTelemetryStop();
  } else if (cmd == "TELEMETRY_STATUS") {
    handleTelemetryStatus();
#endif
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


#ifdef ENABLE_MQTT_TELEMETRY

// Wraps around correctly at millis() rollover, unlike (now >= last + interval).
static bool elapsed(unsigned long now, unsigned long last, unsigned long interval) {
  return (unsigned long)(now - last) >= interval;
}

// Advances the telemetry connection/publish state machine by at most one step.
// Every branch returns promptly -- there is no wait-until-connected loop, so
// serial command handling continues even while WiFi or the broker is down.
void telemetryService() {
  if (!telemetryEnabled) {
    if (telemetryState != TELEM_OFF) {
      if (mqttClient.connected()) mqttClient.disconnect();
      telemetryState = TELEM_OFF;
    }
    return;
  }

  unsigned long now = millis();

  switch (telemetryState) {
    case TELEM_OFF:
      WiFi.mode(WIFI_STA);
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      lastAttemptMs = now;
      telemetryState = TELEM_WIFI_WAIT;
      break;

    case TELEM_WIFI_WAIT:
      if (WiFi.status() == WL_CONNECTED) {
        firstAttempt = true;
        telemetryState = TELEM_MQTT_WAIT;
      } else if (elapsed(now, lastAttemptMs, WIFI_RETRY_MS)) {
        // Association failed or stalled; tear down and retry rather than
        // sitting in a half-connected state forever.
        WiFi.disconnect();
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        lastAttemptMs = now;
      }
      break;

    case TELEM_MQTT_WAIT:
      if (WiFi.status() != WL_CONNECTED) {
        telemetryState = TELEM_OFF;
        break;
      }
      if (firstAttempt || elapsed(now, lastAttemptMs, MQTT_RETRY_MS)) {
        firstAttempt = false;
        lastAttemptMs = now;
        char clientId[32];
        snprintf(clientId, sizeof(clientId), "esp32-telemetry-%06X",
                 (unsigned int)(ESP.getEfuseMac() & 0xFFFFFF));
        if (mqttClient.connect(clientId)) {
          lastPublishMs = now;
          telemetryState = TELEM_STREAMING;
        }
      }
      break;

    case TELEM_STREAMING:
      if (WiFi.status() != WL_CONNECTED) {
        telemetryState = TELEM_OFF;
        break;
      }
      if (!mqttClient.connected()) {
        firstAttempt = true;
        telemetryState = TELEM_MQTT_WAIT;
        break;
      }
      mqttClient.loop();
      if (elapsed(now, lastPublishMs, TELEMETRY_PUBLISH_INTERVAL_MS)) {
        lastPublishMs = now;
        publishTelemetrySample(now);
      }
      break;
  }
}

void publishTelemetrySample(unsigned long now) {
  int value = analogRead(TELEMETRY_ADC_PIN);
  // t_ms is uptime, not wall clock: the ESP32 has no RTC, so the backend
  // stamps receive time and this preserves exact MCU-side sample spacing.
  // seq lets the backend detect dropped messages.
  char payload[128];
  snprintf(payload, sizeof(payload),
           "{\"source\":%d,\"value\":%d,\"t_ms\":%lu,\"seq\":%lu}",
           TELEMETRY_ADC_PIN, value, now, publishedCount);
  if (mqttClient.publish(telemetryTopic, payload)) {
    publishedCount++;
  } else {
    failedPublishCount++;
  }
}

void handleTelemetryStart() {
  if (!telemetryPinAllowed) {
    Serial.println("ERR PIN_NOT_ALLOWED");
    return;
  }
  telemetryEnabled = true;
  Serial.println("OK");
}

void handleTelemetryStop() {
  telemetryEnabled = false;
  Serial.println("OK");
}

void handleTelemetryStatus() {
  const char *stateName = "OFF";
  switch (telemetryState) {
    case TELEM_OFF: stateName = telemetryEnabled ? "STARTING" : "OFF"; break;
    case TELEM_WIFI_WAIT: stateName = "WIFI_WAIT"; break;
    case TELEM_MQTT_WAIT: stateName = "MQTT_WAIT"; break;
    case TELEM_STREAMING: stateName = "STREAMING"; break;
  }
  Serial.print("OK STATE=");
  Serial.print(stateName);
  Serial.print(" WIFI=");
  Serial.print(WiFi.status() == WL_CONNECTED ? 1 : 0);
  Serial.print(" MQTT=");
  Serial.print(mqttClient.connected() ? 1 : 0);
  Serial.print(" IP=");
  Serial.print(WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : String("none"));
  Serial.print(" TOPIC=");
  Serial.print(telemetryTopic);
  Serial.print(" PUBLISHED=");
  Serial.print(publishedCount);
  Serial.print(" FAILED=");
  Serial.println(failedPublishCount);
}

#endif  // ENABLE_MQTT_TELEMETRY
