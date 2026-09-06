"""Debugging utility: fake MCU telemetry publisher.

NOT part of the normal runtime path. Real telemetry originates from the ESP32
firmware. This exists only to exercise the broker and telemetry_service.py
without hardware (implementation plan, Stage 3), and to inject deliberately
malformed payloads when testing the backend's validation.

    python mock_publisher.py                 # publish plausible ADC samples
    python mock_publisher.py --malformed     # publish junk, to test validation
"""

import argparse
import json
import math
import os
import time

import paho.mqtt.client as mqtt

DEFAULT_BROKER = os.environ.get("MQTT_BROKER_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("MQTT_BROKER_PORT", "1884"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broker", default=DEFAULT_BROKER)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--pin", type=int, default=34)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--count", type=int, default=0, help="0 = run until Ctrl+C")
    parser.add_argument(
        "--malformed",
        action="store_true",
        help="publish invalid payloads to verify the backend rejects them safely",
    )
    args = parser.parse_args()

    topic = f"hardware/telemetry/adc/{args.pin}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mock-publisher")
    client.connect(args.broker, args.port, keepalive=60)
    client.loop_start()
    print(f"[mock] publishing to {topic} on {args.broker}:{args.port}", flush=True)

    bad_payloads = [
        b"not json at all",
        b'{"source": 34}',
        b'{"value": 500}',
        b'["not", "an", "object"]',
        b'{"source": "abc", "value": "xyz"}',
    ]

    seq = 0
    started = time.time()
    try:
        while args.count == 0 or seq < args.count:
            if args.malformed:
                payload = bad_payloads[seq % len(bad_payloads)]
            else:
                # Slow sine sweep so logged values visibly change over time.
                elapsed = time.time() - started
                value = int(2048 + 1200 * math.sin(elapsed / 4.0))
                payload = json.dumps(
                    {
                        "source": args.pin,
                        "value": value,
                        "t_ms": int(elapsed * 1000),
                        "seq": seq,
                    },
                    separators=(",", ":"),
                ).encode()
            client.publish(topic, payload, qos=0)
            seq += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"[mock] published {seq} messages", flush=True)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
