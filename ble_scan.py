"""
BLE Scanner — finds your vLinker FD adapter and discovers its GATT services.

Usage:
    python3 ble_scan.py              # Scan for all BLE devices
    python3 ble_scan.py --connect    # Also connect and list GATT services/characteristics

Requires: pip3 install bleak
"""

import asyncio
import argparse
import sys


async def scan_devices(duration: float = 10.0):
    """Scan for nearby BLE devices."""
    from bleak import BleakScanner

    print(f"Scanning for BLE devices ({duration}s)...\n")
    devices = await BleakScanner.discover(timeout=duration, return_adv=True)

    obd_devices = []
    other_devices = []

    for device, adv_data in devices.values():
        entry = {
            "name": device.name or adv_data.local_name or "(unknown)",
            "address": device.address,
            "rssi": adv_data.rssi,
            "service_uuids": adv_data.service_uuids,
        }
        # Flag likely OBD adapters
        name_lower = (entry["name"] or "").lower()
        if any(kw in name_lower for kw in ["vlinker", "obd", "elm", "obdlink", "veepeak", "carista", "lelink"]):
            obd_devices.append(entry)
        else:
            other_devices.append(entry)

    if obd_devices:
        print("=== OBD ADAPTERS FOUND ===\n")
        for d in obd_devices:
            print(f"  Name:     {d['name']}")
            print(f"  Address:  {d['address']}")
            print(f"  RSSI:     {d['rssi']} dBm")
            if d["service_uuids"]:
                print(f"  Services: {', '.join(d['service_uuids'])}")
            print()
    else:
        print("No OBD adapters found in BLE scan.")
        print("Make sure the adapter is plugged in and the car ignition is ON.\n")

    if other_devices:
        print(f"=== OTHER BLE DEVICES ({len(other_devices)}) ===\n")
        for d in sorted(other_devices, key=lambda x: x["rssi"], reverse=True)[:15]:
            name = d["name"] if d["name"] != "(unknown)" else f"(unknown @ {d['address'][:8]}…)"
            print(f"  {name:40s}  RSSI: {d['rssi']:4d} dBm")
        if len(other_devices) > 15:
            print(f"  ... and {len(other_devices) - 15} more")
        print()

    return obd_devices


async def deep_scan(address: str):
    """Connect to a device and enumerate all GATT services and characteristics."""
    from bleak import BleakClient

    print(f"Connecting to {address}...")
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected: {client.is_connected}\n")
        print("=== GATT SERVICES ===\n")

        write_chars = []
        notify_chars = []

        for service in client.services:
            print(f"  Service: {service.uuid}")
            if service.description:
                print(f"    Description: {service.description}")

            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"    Characteristic: {char.uuid}")
                print(f"      Properties: {props}")
                if char.description:
                    print(f"      Description: {char.description}")

                if "write" in char.properties or "write-without-response" in char.properties:
                    write_chars.append(char.uuid)
                if "notify" in char.properties or "indicate" in char.properties:
                    notify_chars.append(char.uuid)
            print()

        # Summary
        print("=== RECOMMENDED UUIDS FOR OBD ===\n")
        if write_chars:
            print(f"  Write characteristic(s):  {', '.join(write_chars)}")
        if notify_chars:
            print(f"  Notify characteristic(s): {', '.join(notify_chars)}")

        if write_chars and notify_chars:
            print(f"\n  Try this command:")
            print(f"    python3 ble_obd_test.py --address {address} \\")
            print(f"        --write-uuid {write_chars[0]} \\")
            print(f"        --notify-uuid {notify_chars[0]}")

        # Quick test: send ATZ and see if we get a response
        if write_chars and notify_chars:
            print("\n=== QUICK TEST: Sending ATZ (reset) ===\n")
            response_data = bytearray()

            def on_notify(sender, data):
                response_data.extend(data)

            await client.start_notify(notify_chars[0], on_notify)
            await asyncio.sleep(0.3)

            # Send ATZ\r
            cmd = b"ATZ\r"
            print(f"  Sending: {cmd!r}")
            try:
                await client.write_gatt_char(write_chars[0], cmd, response=False)
            except Exception:
                # Some chars need write-with-response
                await client.write_gatt_char(write_chars[0], cmd, response=True)

            await asyncio.sleep(2.0)
            await client.stop_notify(notify_chars[0])

            if response_data:
                print(f"  Response: {bytes(response_data)!r}")
                decoded = response_data.decode("ascii", errors="replace").strip()
                print(f"  Decoded:  {decoded}")
                print(f"\n  ✓ Adapter is responding over BLE!")
            else:
                print(f"  No response (adapter may need ignition ON for power)")


async def main():
    parser = argparse.ArgumentParser(description="BLE scanner for OBD adapters")
    parser.add_argument("--connect", action="store_true",
                        help="Connect to the first OBD adapter found and enumerate GATT services")
    parser.add_argument("--address", type=str, default=None,
                        help="Connect to a specific BLE device address/UUID")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Scan duration in seconds (default: 10)")
    args = parser.parse_args()

    if args.address:
        await deep_scan(args.address)
        return

    obd_devices = await scan_devices(duration=args.duration)

    if args.connect and obd_devices:
        addr = obd_devices[0]["address"]
        print(f"--- Deep-scanning first OBD adapter: {obd_devices[0]['name']} ---\n")
        await deep_scan(addr)
    elif args.connect and not obd_devices:
        print("No OBD adapter found to connect to.")
        print("Try: python3 ble_scan.py --address <DEVICE_UUID>")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except ImportError:
        print("ERROR: 'bleak' is not installed.")
        print("Install it with: pip3 install bleak")
        sys.exit(1)
