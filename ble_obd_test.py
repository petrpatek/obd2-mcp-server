"""
BLE OBD Test — send AT commands to a BLE OBD adapter over GATT.

Usage:
    python3 ble_obd_test.py --address <DEVICE_UUID>
    python3 ble_obd_test.py --address <DEVICE_UUID> --write-uuid 0000fff2-... --notify-uuid 0000fff1-...

If UUIDs are not specified, tries common OBD BLE UUIDs automatically.

Requires: pip3 install bleak
"""

import asyncio
import argparse
import sys

# Common BLE GATT UUIDs used by ELM327-based OBD adapters
KNOWN_UUID_SETS = [
    {
        "name": "FFF0/FFF1/FFF2 (vLinker, OBDLink CX)",
        "service": "0000fff0-0000-1000-8000-00805f9b34fb",
        "notify": "0000fff1-0000-1000-8000-00805f9b34fb",
        "write": "0000fff2-0000-1000-8000-00805f9b34fb",
    },
    {
        "name": "FFE0/FFE1 (LeLink, generic Chinese adapters)",
        "service": "0000ffe0-0000-1000-8000-00805f9b34fb",
        "notify": "0000ffe1-0000-1000-8000-00805f9b34fb",
        "write": "0000ffe1-0000-1000-8000-00805f9b34fb",  # same char for read/write
    },
    {
        "name": "E7810A71 (Veepeak/custom)",
        "service": "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
        "notify": "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
        "write": "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
    },
]


async def try_uuid_set(client, uuid_set: dict) -> bool:
    """Try to communicate using a specific set of UUIDs. Returns True on success."""
    name = uuid_set["name"]
    notify_uuid = uuid_set["notify"]
    write_uuid = uuid_set["write"]

    # Check if the characteristics exist
    try:
        write_char = client.services.get_characteristic(write_uuid)
        notify_char = client.services.get_characteristic(notify_uuid)
        if not write_char or not notify_char:
            return False
    except Exception:
        return False

    print(f"  Trying UUID set: {name}")
    response_data = bytearray()

    def on_notify(sender, data):
        response_data.extend(data)

    try:
        await client.start_notify(notify_uuid, on_notify)
        await asyncio.sleep(0.2)

        # Send ATZ (reset)
        await client.write_gatt_char(write_uuid, b"ATZ\r", response=False)
        await asyncio.sleep(2.0)

        await client.stop_notify(notify_uuid)

        if response_data:
            decoded = bytes(response_data).decode("ascii", errors="replace").strip()
            print(f"  ✓ Response: {decoded}")
            return True
        else:
            print(f"  ✗ No response")
            return False
    except Exception as e:
        print(f"  ✗ Error: {e}")
        try:
            await client.stop_notify(notify_uuid)
        except Exception:
            pass
        return False


async def interactive_session(client, write_uuid: str, notify_uuid: str):
    """Interactive AT command session over BLE."""
    print("\n=== Interactive OBD Session (BLE) ===")
    print("Type AT commands (ATZ, ATI, ATRV, 0100, etc.)")
    print("Type 'quit' to exit.\n")

    response_buffer = bytearray()
    response_event = asyncio.Event()

    def on_notify(sender, data):
        response_buffer.extend(data)
        # ELM327 ends responses with '>' prompt
        if b">" in data:
            response_event.set()

    await client.start_notify(notify_uuid, on_notify)

    # Test commands to run automatically first
    auto_cmds = [
        ("ATZ", "Reset adapter"),
        ("ATI", "Adapter info"),
        ("ATE0", "Echo off"),
        ("ATRV", "Battery voltage"),
        ("ATSP0", "Auto-detect protocol"),
        ("0100", "Supported PIDs [01-20]"),
    ]

    for cmd, desc in auto_cmds:
        response_buffer.clear()
        response_event.clear()

        print(f">>> {cmd}  ({desc})")
        await client.write_gatt_char(write_uuid, f"{cmd}\r".encode(), response=False)

        try:
            await asyncio.wait_for(response_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        if response_buffer:
            decoded = bytes(response_buffer).decode("ascii", errors="replace").strip()
            # Clean up the output
            lines = [l.strip() for l in decoded.split("\r") if l.strip() and l.strip() != ">"]
            for line in lines:
                print(f"    {line}")
        else:
            print(f"    (no response)")
        print()

    await client.stop_notify(notify_uuid)


async def main():
    from bleak import BleakClient

    parser = argparse.ArgumentParser(description="BLE OBD tester")
    parser.add_argument("--address", required=True, help="BLE device address/UUID")
    parser.add_argument("--write-uuid", default=None, help="GATT write characteristic UUID")
    parser.add_argument("--notify-uuid", default=None, help="GATT notify characteristic UUID")
    args = parser.parse_args()

    print(f"Connecting to {args.address}...")
    async with BleakClient(args.address, timeout=15.0) as client:
        print(f"Connected: {client.is_connected}\n")

        if args.write_uuid and args.notify_uuid:
            # Use specified UUIDs directly
            await interactive_session(client, args.write_uuid, args.notify_uuid)
        else:
            # Auto-detect: try known UUID sets
            print("Auto-detecting GATT UUIDs...\n")
            for uuid_set in KNOWN_UUID_SETS:
                success = await try_uuid_set(client, uuid_set)
                if success:
                    print(f"\n  ✓ Found working UUID set: {uuid_set['name']}")
                    print(f"    Write:  {uuid_set['write']}")
                    print(f"    Notify: {uuid_set['notify']}\n")
                    await interactive_session(client, uuid_set["write"], uuid_set["notify"])
                    return

            print("\nNo known UUID sets worked. Run ble_scan.py --connect to discover the correct UUIDs.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except ImportError:
        print("ERROR: 'bleak' is not installed.")
        print("Install it with: pip3 install bleak")
        sys.exit(1)
