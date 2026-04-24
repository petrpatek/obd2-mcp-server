"""Quick diagnostic: test raw serial communication with the OBD adapter."""
import serial
import sys
import time

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/tty.vLinkerFD-Android"

for baud in [500000, 115200, 38400, 9600]:
    print(f"\n--- Trying {port} @ {baud} baud ---")
    try:
        ser = serial.Serial(port, baudrate=baud, timeout=3)
        time.sleep(0.5)  # Let the adapter wake up

        # Send ATZ (reset) command
        ser.write(b"ATZ\r")
        time.sleep(1)
        response = ser.read(100)
        print(f"  ATZ response: {response!r}")

        if response:
            # Try ATI (adapter info)
            ser.write(b"ATI\r")
            time.sleep(0.5)
            response = ser.read(100)
            print(f"  ATI response: {response!r}")

            ser.close()
            print(f"\n  ✓ Adapter responded at {baud} baud!")
            sys.exit(0)

        ser.close()
        print(f"  No response at {baud}")
    except Exception as e:
        print(f"  Error: {e}")

print("\n✗ No response at any baud rate.")
print("  Check: ignition ON? Bluetooth connected? Adapter LED on?")
