"""Backend telemetry service for Architecture B (asynchronous MQTT sensing).

Subscribes to the MCU's continuous ADC telemetry stream and appends every
valid observation to a line-oriented .txt log.

    MQTT broker -> validate -> extract observation -> append -> adc_stream.txt

This service is deliberately independent of the existing synchronous
architecture: it does not import agent/, bridge/, or skills/, holds no serial
port, and performs no LLM reasoning or actuator control. Architecture A keeps
working whether or not this service (or the broker) is running.

Run:
    python telemetry_service.py
    python telemetry_service.py --broker 192.168.1.5 --out logs/adc_stream.txt
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

_HERE = Path(__file__).resolve().parent

DEFAULT_BROKER = os.environ.get("MQTT_BROKER_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("MQTT_BROKER_PORT", "1884"))
# '+' matches any single level, so one subscription covers every ADC pin the
# firmware might publish -- keeps the backend pin-generic like the primitives.
DEFAULT_TOPIC = os.environ.get("MQTT_TOPIC", "hardware/telemetry/adc/+")
DEFAULT_LOG = os.environ.get("MQTT_LOG_FILE", str(_HERE / "logs" / "adc_stream.txt"))


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class TelemetryWriter:
    """Appends validated observations to a JSON-per-line text file.

    The handle is kept open and flushed after every line so the file reflects
    live telemetry while it is being tailed.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._fh = None
        self.written = 0
        self.rejected = 0

    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Append: previously captured readings survive a service restart.
        self._fh = open(self.path, "a", encoding="utf-8")
        log(f"logging observations to {self.path}")

    def write(self, record):
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._fh.flush()
            self.written += 1
        except OSError as exc:
            # A failing disk must not kill the subscriber; keep consuming so a
            # transient write error doesn't also cost us the live stream.
            log(f"WARN could not write observation: {exc}")

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def parse_observation(topic, payload_bytes):
    """Validate one MQTT payload into a log record, or return None.

    Required fields are 'source' (which hardware input) and 'value' (what was
    observed). 'timestamp' is stamped here on receipt: the MCU has no RTC, so
    it reports monotonic uptime ('t_ms') instead and the backend supplies
    wall-clock time.
    """
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"payload is not valid JSON ({exc})"

    if not isinstance(payload, dict):
        return None, "payload is not a JSON object"

    if "source" not in payload:
        return None, "missing required field 'source'"
    if "value" not in payload:
        return None, "missing required field 'value'"

    try:
        source = int(payload["source"])
        value = int(payload["value"])
    except (TypeError, ValueError):
        return None, "'source'/'value' are not numeric"

    record = {
        "timestamp": round(time.time(), 3),
        "source": source,
        "value": value,
    }
    # Pass through MCU-side sequencing when present: t_ms preserves the exact
    # spacing the MCU sampled at, seq exposes dropped messages.
    for optional in ("t_ms", "seq"):
        if optional in payload:
            try:
                record[optional] = int(payload[optional])
            except (TypeError, ValueError):
                pass
    record["topic"] = topic
    return record, None


def build_client(args, writer):
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"telemetry-service-{os.getpid()}",
    )

    def on_connect(_client, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            log(f"connected to broker {args.broker}:{args.port}")
            # Subscribe inside on_connect so the subscription is restored
            # automatically after an auto-reconnect.
            _client.subscribe(args.topic, qos=0)
            log(f"subscribed to {args.topic}")
        else:
            log(f"WARN broker refused connection: {reason_code}")

    def on_disconnect(_client, _userdata, _flags, reason_code, _properties):
        if reason_code != 0:
            log(f"WARN lost broker connection ({reason_code}); reconnecting")

    def on_message(_client, _userdata, msg):
        record, error = parse_observation(msg.topic, msg.payload)
        if record is None:
            writer.rejected += 1
            # Malformed traffic is expected on a shared topic and must never
            # terminate the service.
            log(f"WARN discarded message on {msg.topic}: {error}")
            return
        writer.write(record)
        if writer.written == 1 or writer.written % args.report_every == 0:
            log(
                f"{writer.written} observations logged "
                f"(latest: source={record['source']} value={record['value']})"
            )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broker", default=DEFAULT_BROKER)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--out", default=DEFAULT_LOG)
    parser.add_argument(
        "--report-every",
        type=int,
        default=20,
        help="print a progress line every N observations",
    )
    args = parser.parse_args()

    writer = TelemetryWriter(args.out)
    try:
        writer.open()
    except OSError as exc:
        log(f"FATAL cannot open log file {args.out}: {exc}")
        return 1

    client = build_client(args, writer)

    def shutdown(_signum, _frame):
        log(
            f"stopping: {writer.written} observations logged, "
            f"{writer.rejected} rejected"
        )
        client.disconnect()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        client.connect(args.broker, args.port, keepalive=60)
    except OSError as exc:
        log(f"FATAL cannot reach broker at {args.broker}:{args.port}: {exc}")
        log("is the broker running?  mosquitto -c mosquitto.conf -v")
        writer.close()
        return 1

    log("waiting for telemetry (Ctrl+C to stop)")
    try:
        # loop_forever handles reconnection internally, so a broker restart
        # resumes the stream without restarting this service.
        client.loop_forever()
    finally:
        writer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
