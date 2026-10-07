// Generic hardware primitives over serial: PING and SHIFT_OUT, with a fixed pin allowlist.
#include <Arduino.h>

const int ALLOWED_PINS[] = {25, 26, 27};
const int NUM_ALLOWED = sizeof(ALLOWED_PINS) / sizeof(ALLOWED_PINS[0]);
const int LINE_SIZE = 256;
const int MAX_BYTES = 64;
const int MAX_TOKENS = 7;
const int MAX_INT_DIGITS = 9;

char lineBuf[LINE_SIZE];
int lineLen = 0;
bool discarding = false;

bool isAllowed(int pin) {
  for (int i = 0; i < NUM_ALLOWED; i++) {
    if (ALLOWED_PINS[i] == pin) {
      return true;
    }
  }
  return false;
}

bool parseInt(const char* s, int* out) {
  int len = strlen(s);
  if (len < 1 || len > MAX_INT_DIGITS) {
    return false;
  }
  int value = 0;
  for (int i = 0; i < len; i++) {
    if (s[i] < '0' || s[i] > '9') {
      return false;
    }
    value = value * 10 + (s[i] - '0');
  }
  *out = value;
  return true;
}

int hexValue(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

// Splits on single spaces in place. Returns the token count, or MAX_TOKENS + 1 if there are too many.
int tokenize(char* line, char* tokens[]) {
  int count = 0;
  char* start = line;
  while (true) {
    if (count == MAX_TOKENS) {
      return MAX_TOKENS + 1;
    }
    tokens[count++] = start;
    char* space = strchr(start, ' ');
    if (space == NULL) {
      return count;
    }
    *space = '\0';
    start = space + 1;
  }
}

void handleShiftOut(char* tokens[], int count) {
  if (count != 6) {
    Serial.println("ERR BAD_ARGS");
    return;
  }
  int data, clock, latch, group;
  if (!parseInt(tokens[1], &data) || !parseInt(tokens[2], &clock) ||
      !parseInt(tokens[3], &latch) || !parseInt(tokens[4], &group)) {
    Serial.println("ERR BAD_ARGS");
    return;
  }
  if (!isAllowed(data) || !isAllowed(clock) || !isAllowed(latch)) {
    Serial.println("ERR PIN_NOT_ALLOWED");
    return;
  }
  if (data == clock || data == latch || clock == latch) {
    Serial.println("ERR BAD_ARGS");
    return;
  }
  if (group < 1) {
    Serial.println("ERR BAD_ARGS");
    return;
  }
  const char* hex = tokens[5];
  int hexLen = strlen(hex);
  if (hexLen < 2 || hexLen % 2 != 0) {
    Serial.println("ERR BAD_ARGS");
    return;
  }
  for (int i = 0; i < hexLen; i++) {
    if (hexValue(hex[i]) < 0) {
      Serial.println("ERR BAD_ARGS");
      return;
    }
  }
  int numBytes = hexLen / 2;
  if (numBytes > MAX_BYTES || numBytes % group != 0) {
    Serial.println("ERR BAD_ARGS");
    return;
  }

  uint8_t bytes[MAX_BYTES];
  for (int i = 0; i < numBytes; i++) {
    bytes[i] = (hexValue(hex[2 * i]) << 4) | hexValue(hex[2 * i + 1]);
  }
  for (int g = 0; g < numBytes; g += group) {
    digitalWrite(latch, LOW);
    for (int i = g; i < g + group; i++) {
      for (int bit = 7; bit >= 0; bit--) {
        digitalWrite(data, (bytes[i] >> bit) & 1);
        digitalWrite(clock, HIGH);
        digitalWrite(clock, LOW);
      }
    }
    digitalWrite(latch, HIGH);
  }
  Serial.println("OK");
}

void handleLine(char* line) {
  char* tokens[MAX_TOKENS];
  int count = tokenize(line, tokens);
  if (strcmp(tokens[0], "PING") == 0) {
    Serial.println(count == 1 ? "OK PONG" : "ERR BAD_ARGS");
  } else if (strcmp(tokens[0], "SHIFT_OUT") == 0) {
    handleShiftOut(tokens, count);
  } else {
    Serial.println("ERR UNKNOWN_COMMAND");
  }
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < NUM_ALLOWED; i++) {
    pinMode(ALLOWED_PINS[i], OUTPUT);
  }
  digitalWrite(25, LOW);
  digitalWrite(26, LOW);
  digitalWrite(27, HIGH);
  Serial.println("ESP32_READY");
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      if (discarding) {
        discarding = false;
        Serial.println("ERR BAD_ARGS");
      } else {
        if (lineLen > 0 && lineBuf[lineLen - 1] == '\r') {
          lineLen--;
        }
        lineBuf[lineLen] = '\0';
        if (lineLen > 0) {
          handleLine(lineBuf);
        }
      }
      lineLen = 0;
    } else if (!discarding) {
      if (lineLen < LINE_SIZE - 1) {
        lineBuf[lineLen++] = c;
      } else {
        discarding = true;
      }
    }
  }
}
