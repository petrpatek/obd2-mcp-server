"""
BLE Scanner — finds BLE OBD adapters (vLinker FD etc.) and dumps their
GATT service/characteristic layout.

Usage:
    python3 ble_scan.py                        # Scan for nearby devices
    python3 ble_scan.py --connect              # Connect to first OBD match
    python3 ble_scan.py --address <UUID>       # Deep-scan a specific device

Requires: pip install bleak
"""

from __future__ import annotations

import argparse
import asyncio
import sys

# Share the known profile table with the runtime connection so they stay
# in sync.
try:
    from obd2_mcp.ble_connection import (
        KNOWN_UUID_SETS,
        OBD_NAME_PATTERNS,
        OBD_SERVICE_UUIDS,
    )
except ImportError:
    # Fallback if run outside the package (setup.sh hasn't been used yet)
    OBD_NAME_PATTERNS = [
        "vlinker", "obdlink", "veepeak", "elm327", "elm329",
        "obd", "carista", "lelink", "konnwei", "ancel",
        "bluedriver", "fixd", "bafx", "vgate", "icar",
    ]
    KNOWN_UUID_SETS = [
        {"name": "FFF0",
         "service": "0000fff0-0000-1000-8000-00805f9b34fb",
         "notify": "0000fff1-0000-1000-8000-00805f9b34fb",
         "write": "0000fff2-0000-1000-8000-00805f9b34fb"},
        {"name": "FFE0",
         "service": "0000ffe0-0000-1000-8000-00805f9b34fb",
         "notify": "0000ffe1-0000-1000-8000-00805f9b34fb",
         "write": "0000ffe1-0000-1000-8000-00805f9b34fb"},
        {"name": "NUS",
         "service": "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
         "notify": "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
         "write": "6e400002-b5a3-f393-e0a9-e50e24dcca9e"},
    ]
    OBD_SERVICE_UUIDS = [s["service"] for s in KNOWN_UUID_SETS]


def _is_probably_obd(name: str, service_uuids: list[str]) -> str | None:
    """Return a match reason if a device looks like an OBD adapter, else None."""
    name_lower = (name or "").lower()
    if any(p in name_lower for p in OBD_NAME_PATTERNS):
        return "name"
    svc_set = {u.lower() for u in (service_uuids or [])}
    if svc_set & {u.lower() for u in OBD_SERVICE_UUIDS}:
        return "service-uuid"
    return None


async def scan_devices(duration: float = 12.0) -> list[dict]:
    """Scan for nearby BLE devices. Returns list of likely OBD adapters."""
    from bleak import BleakScanner

    print(f"Scanning for BLE devices ({duration:.0f}s)...\n")
    devices = await BleakScanner.discover(timeout=duration, return_adv=True)

    obd_devices: list[dict] = []
    other_devices: list[dict] = []

    for device, adv_data in devices.values():
        name = device.name or adv_data.local_name or "(unknown)"
        entry = {
            "name": name,
            "address": device.address,
            "rssi": adv_data.rssi,
            "service_uuids": adv_data.service_uuids or [],
        }
        match = _is_probably_obd(name, entry["service_uuids"])
        if match:
            entry["match_reason"] = match
            obd_devices.append(entry)
        else:
            other_devices.append(entry)

    if obd_devices:
        print("=== OBD ADAPTERS FOUND ===\n")
        for d in obd_devices:
            print(f"  Name:         {d['name']}")
            print(f"  Address:      {d['address']}")
            print(f"  RSSI:         {d['rssi']} dBm")
            print(f"  Match reason: {d['match_reason']}")
            if d["service_uuids"]:
                print(f"  Services:     {', '.join(d['service_uuids'])}")
            print()
    else:
        print("No OBD adapters found.\n")
        print("Checklist:")
        print("  1. Adapter plugged into OBD port + ignition ON")
        print("  2. Adapter NOT paired as classic Bluetooth SPP")
        print("     (System Settings → Bluetooth → Forget it)")
        print("  3. Terminal has Bluetooth permission")
        print("     (System Settings → Privacy & Security → Bluetooth)")
        print()

    if other_devices:
        print(f"=== OTHER BLE DEVICES ({len(other_devices)}) ===\n")
        for d in sorted(other_devices, key=lambda x: x["rssi"], reverse=True)[:15]:
            name = d["name"] if d["name"] != "(unknown)" else f"(unknown @ {str(d['address'])[:8]}…)"
            print(f"  {name:40s}  RSSI: {d['rssi']:4d} dBm")
        if len(other_devices) > 15:
            print(f"  ... and {len(other_devices) - 15} more")
        print()

    return obd_devices


async def deep_scan(address: str) -> None:
    """Connect to a device and enumerate all GATT services and characteristics."""
    from bleak import BleakClient

    print(f"Connecting to {address}...")
    async with BleakClient(address, timeout=20.0) as client:
        print(f"Connected: {client.is_connected}")
        try:
            print(f"Negotiated MTU: {client.mtu_size} bytes\n")
        except Exception:  # noqa: BLE001
            print()

        print("=== GATT SERVICES ===\n")

        write_chars: list[str] = []
        notify_chars: list[str] = []
        known_profile: dict | None = None

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

            # Match against known profiles
            for uuid_set in KNOWN_UUID_SETS:
                if service.uuid.lower() == uuid_set["service"].lower():
                    known_profile = uuid_set
                    break

        print("=== RECOMMENDED UUIDS FOR OBD ===\n")
        if known_profile:
            print(f"  Matched known profile: {known_profile['name']}")
            print(f"    Write:  {known_profile['write']}")
            print(f"    Notify: {known_profile['notify']}\n")
            write_uuid = known_profile["write"]
            notify_uuid = known_profile["notify"]
        elif write_chars and notify_chars:
            write_uuid = write_chars[0]
            notify_uuid = notify_chars[0]
            print(f"  Auto-detected (first write/notify pair):")
            print(f"    Write:  {write_uuid}")
            print(f"    Notify: {notify_uuid}\n")
        else:
            write_uuid = notify_uuid = None
            print("  Could not find a write+notify pair.\n")

        if write_uuid and notify_uuid:
            print(f"  Suggested next step:")
            print(f"    python3 ble_obd_test.py --address {address} \\")
            print(f"        --write-uuid {write_uuid} \\")
            print(f"        --notify-uuid {notify_uuid}\n")

        # Quick test: send ATZ and see if we get a '>' prompt back
        if write_uuid and notify_uuid:
            print("=== QUICK TEST: ATZ (reset) ===\n")
            buffer = bytearray()
            done = asyncio.Event()

            def on_notify(_sender, data: bytearray) -> None:
                buffer.extend(data)
                if b">" in buffer:
                    done.set()

            await client.start_notify(notify_uuid, on_notify)
            await asyncio.sleep(0.3)  # let subscription settle

            print("  Sending: b'ATZ\\r'")
            try:
                await client.write_gatt_char(write_uuid, b"ATZ\r", response=False)
            except Exception:  # noqa: BLE001
                await client.write_gatt_char(write_uuid, b"ATZ\r", response=True)

            try:
                await asyncio.wait_for(done.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

            await client.stop_notify(notify_uuid)

            if buffer:
                print(f"  Raw:     {bytes(buffer)!r}")
                decoded = buffer.decode("ascii", errors="replace").strip()
                print(f"  Decoded: {decoded}")
                if "ELM" in decoded.upper():
                    print("\n  ✓ Adapter is a live ELM327 over BLE!")
                else:
                    print("\n  ⚠ Got data, but no 'ELM' banner. Adapter may be in an "
                          "unexpected state — try again with ignition ON.")
            else:
                print("  ⚠ No response. Most likely: ignition is OFF (adapter has "
                      "no power), or the write/notify UUIDs are wrong for this device.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="BLE scanner for OBD adapters")
    parser.add_argument("--connect", action="store_true",
                        help="Also connect to the first OBD adapter found "
                             "and enumerate its GATT services.")
    parser.add_argument("--address", type=str, default=None,
                        help="Deep-scan a specific BLE device by address/UUID.")
    parser.add_argument("--duration", type=float, default=12.0,
                        help="Scan duration in seconds (default: 12).")
    args = parser.parse_args()

    if args.address:
        await deep_scan(args.address)
        return

    obd_devices = await scan_devices(duration=args.duration)

    if args.connect and obd_devices:
        first = obd_devices[0]
        print(f"--- Deep-scanning first OBD adapter: {first['name']} ---\n")
        await deep_scan(first["address"])
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
        print("Install it with: pip install bleak")
        sys.exit(1)
