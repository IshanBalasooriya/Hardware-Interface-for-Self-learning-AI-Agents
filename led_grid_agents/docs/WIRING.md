# Wiring and calibration

## Wiring

| MAX7219 module | ESP32 DevKit (`esp32doit-devkit-v1`) |
|---|---|
| VCC | 5V / VIN |
| GND | GND |
| DIN | GPIO 25 (`DATA_PIN`) |
| CLK | GPIO 26 (`CLOCK_PIN`) |
| CS | GPIO 27 (`LATCH_PIN`) |

Serial: USB, 115200 baud, newline-terminated ASCII lines. The firmware prints `ESP32_READY` on boot.

## Orientation calibration

Send each line from a serial monitor (in `firmware/`: `pio device monitor`) and expect `OK` after each one.

1. `SHIFT_OUT 25 26 27 2 0F0009000B070A020C01` then `SHIFT_OUT 25 26 27 2 01000200030004000500060007000800` to wake and clear the chip.
2. `SHIFT_OUT 25 26 27 2 01FF`: one full line of 8 LEDs lights. Rotate the module until that line is horizontal and at the **top**.
3. `SHIFT_OUT 25 26 27 2 0180`: one LED stays lit in the top row. Note whether it is at the **left** or the **right** end.
4. `SHIFT_OUT 25 26 27 2 0100`: clears the top row.
5. `SHIFT_OUT 25 26 27 2 0880`: the lit LED is in the **bottom** row.

Set `MSB_IS_LEFT` in `config.py` to `True` if the LED in step 3 was at the left end, and `False` if it was at the right end.

## Calibration result

Calibrated on 2026-10-07 (stage 3, H3):

- Orientation: the module is rotated so that the row driven by register `0x01` is the top edge. Register `0x08` is the bottom row.
- Step 3 (`0180`): the LED was at the **left** end of the top row, so bit 7 is the leftmost LED.
- Step 5 (`0880`): the LED was at the bottom-left.
- Result: `MSB_IS_LEFT = True`.
