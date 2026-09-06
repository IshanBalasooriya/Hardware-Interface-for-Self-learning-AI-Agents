# Architecture B — Asynchronous MQTT Sensor Telemetry

Second, independent observation architecture for the POC. The MCU samples the
ADC on its own schedule and publishes to an MQTT broker; a standalone backend
subscriber persists every observation to a text log.

```
ADC → MCU continuous sampling → MQTT → broker → telemetry_service.py → logs/adc_stream.txt
```

The existing synchronous architecture (Architecture A) is unchanged and does
not depend on any of this. Neither architecture requires the other to run.

| | Architecture A (existing) | Architecture B (new) |
|---|---|---|
| Trigger | host asks (`read_analog`) | MCU publishes continuously |
| Path | LLM → registry → serial → MCU | MCU → MQTT → broker → backend |
| Output | tool result in LLM context | `logs/adc_stream.txt` |
| Firmware env | `esp32doit-devkit-v1` | `esp32doit-devkit-v1-mqtt` |
| Needs broker | no | yes |

## Files

| File | Purpose |
|---|---|
| `telemetry_service.py` | MQTT subscriber → validates → appends to `logs/adc_stream.txt` |
| `mock_publisher.py` | Debug-only fake publisher. Not part of the real runtime path |
| `mosquitto.conf` | Broker config: binds `0.0.0.0:1884`, anonymous access |
| `setup_firewall.ps1` | One-time elevated step so the ESP32 can reach the broker |
| `logs/adc_stream.txt` | Captured telemetry (gitignored, created on first run) |

## One-time setup

**1. Broker** — already installed (`winget install EclipseFoundation.Mosquitto`).

Port **1884**, not the default 1883: the installed *Mosquitto Broker* Windows
service already holds 1883 and binds localhost-only. Using 1884 leaves that
service untouched and avoids needing admin to stop it.

**2. Firewall** (only needed for the real MCU, not for mock testing) — in an
**Administrator** PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File setup_firewall.ps1
```

**3. Python dependency**

```bash
POC/.venv/Scripts/python.exe -m pip install "paho-mqtt>=2.0"
```

**4. Firmware config** — copy the template and fill it in:

```bash
cd POC/firmware/esp32_generic_functions
copy include\telemetry_config.example.h include\telemetry_config.h
```

Set `WIFI_SSID` / `WIFI_PASSWORD` (2.4 GHz WPA2-PSK — the ESP32 cannot join
5 GHz-only or WPA2-Enterprise networks) and set `MQTT_BROKER_HOST` to this
machine's **LAN IP** on the network the ESP32 joins (`ipconfig`), never
`127.0.0.1`.

## Running Architecture B

Three terminals, in order:

```bash
# 1. broker
cd POC/Temporaral_Context_Approach
"C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto.conf -v

# 2. backend subscriber
cd POC/Temporaral_Context_Approach
../.venv/Scripts/python.exe telemetry_service.py

# 3. flash the telemetry firmware (once)
cd POC/firmware/esp32_generic_functions
~/.platformio/penv/Scripts/pio.exe run -e esp32doit-devkit-v1-mqtt -t upload
```

Then watch observations accumulate:

```bash
tail -f POC/Temporaral_Context_Approach/logs/adc_stream.txt
```

Telemetry starts automatically at boot (`TELEMETRY_AUTOSTART`). It can also be
controlled at runtime over serial without reflashing:

| Command | Effect |
|---|---|
| `TELEMETRY_START` | begin publishing |
| `TELEMETRY_STOP` | stop publishing, disconnect from broker |
| `TELEMETRY_STATUS` | `OK STATE=STREAMING WIFI=1 MQTT=1 IP=… PUBLISHED=… FAILED=…` |

### Log format

JSON-per-line. `timestamp` is backend receive time (the ESP32 has no RTC);
`t_ms` is MCU uptime, preserving exact sample spacing; `seq` exposes drops.

```json
{"timestamp":1788640244.436,"source":34,"value":2316,"t_ms":902,"seq":3,"topic":"hardware/telemetry/adc/34"}
```

Topic is `hardware/telemetry/adc/34` — hardware-descriptive, never
interpreted meaning. The backend subscribes to `hardware/telemetry/adc/+` so
it stays pin-generic.

## Running Architecture A (unchanged)

Needs no broker, no WiFi, no telemetry service. Stop the broker entirely and
it still works:

```bash
cd POC/firmware/esp32_generic_functions
~/.platformio/penv/Scripts/pio.exe run -e esp32doit-devkit-v1 -t upload   # serial-only build

cd POC
.venv/Scripts/python.exe agent/agent_loop.py         # discovery loop
.venv/Scripts/python.exe agent/server.py             # dashboard
```

The telemetry firmware build is a **superset**: all serial primitives
(`READ_ADC`, `SET_PWM`, …) keep working on it, so Architecture A is usable on
either firmware build. Only one process may hold the serial port at a time.

## Debugging without hardware

```bash
../.venv/Scripts/python.exe mock_publisher.py                # plausible samples
../.venv/Scripts/python.exe mock_publisher.py --malformed    # test validation
```

## Known limitations

- **Firewall step needs admin.** Without it the ESP32's connection is silently
  dropped; the board will sit in `MQTT_WAIT` and `TELEMETRY_STATUS` shows
  `MQTT=0`.
- **Broker runs in the foreground.** `mosquitto -d` is unsupported on Windows,
  so the broker terminal must stay open.
- **A failed broker connect can stall the MCU for up to ~1s.**
  `PubSubClient::connect()` is blocking; `setSocketTimeout(1)` bounds it and
  retries are spaced 5s apart, so serial stays responsive, but this is not a
  hard real-time guarantee.
- **No backpressure.** If the broker is unreachable, samples are dropped, not
  queued — `seq` gaps in the log show what was lost. QoS 0, no persistence.
- **Wall-clock timestamps come from the backend**, so they include network and
  scheduling jitter. Use `t_ms` for precise inter-sample spacing.
- **Telemetry obeys the firmware pin allowlist.** If `TELEMETRY_ADC_PIN` is not
  in `ALLOWED_ADC_PINS`, telemetry refuses to start and `TELEMETRY_START`
  returns `ERR PIN_NOT_ALLOWED`.
