import serial
import time


def main():
    port = "COM3"
    baudrate = 115200

    with serial.Serial(port, baudrate, timeout=1) as ser:
        print(f"Connected to {port} at {baudrate} baud")
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if line:
                print(f"Received: {line}")
            time.sleep(0.1)


if __name__ == "__main__":
    main()
