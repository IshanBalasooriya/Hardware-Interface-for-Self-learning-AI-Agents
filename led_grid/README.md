# LED Grid System

An LLM agent controls an 8x8 MAX7219 LED matrix on an ESP32 through generic hardware primitives (`shift_out`, `wait`), observes the confirmed result as an LED map, and saves what works as reusable JSON skills that replay without the LLM. It is the demonstration vehicle for a general LLM-to-hardware interface; see `docs/00_MASTER.md`.

## Setup

```
pip install -r requirements.txt
pytest
```
