"""
BLE OBD Test — send AT/OBD commands to a BLE OBD adapter over GATT.

Usage:
    python3 ble_obd_test.py --address <DEVICE_UUID>
    python3 ble_obd_test.py --address <UUID> \
        --write-uuid 0000fff2-... --notify-uuid 0000fff1-...

If UUIDs are not specified, tries known OBD BLE profiles automatically.

Requires: pip install bleak
"""

from __future__ import annotations

import argparse
import asyncio
import sys

try:
    from obd2_mcp.ble_connection import KNOWN_UUID_SETS
except ImportError:
    KNOWN_UUID_SETS = [
        {"name": "FFF0 (vLinker FD, OBDLink CX)",
         "service": "0000fff0-0000-1000-8000-00805f9b34fb",
         "notify": "0000fff1-0000-1000-8000-00805f9b34fb",
         "write": "0000fff2-0000-1000-8000-00805f9b34fb"},
        {"name": "FFE0 (LeLink/HM-10)",
         "service": "0000ffe0-0000-1000-8000-00805f9b34fb",
         "notify": "0000ffe1-0000-1000-8000-00805f9b34fb",
         "write": "0000ffe1-0000-1000-8000-00805f9b34fb"},
        {"name": "NUS (Nordic UART)",
         "service": "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
         "notify": "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
         "write": "6e400002-b5a3-f393-e0a9-e50e24dcca9e"},
    ]


# ---------------------------------------------------------------------------
# Framing: a read-until-'>' helper shared by both test and interactive modes
# ---------------------------------------------------------------------------

class _Framer:
    """Accumulates BLE notify bytes, signals when a full '>'-terminated
    ELM327 response is available."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.event = asyncio.Event()

    def on_notify(self, _sender, data: bytearray) -> None:
        self.buffer.extend(data)
        if b">" in self.buffer:
            self.event.set()

    def take(self) -> str:
        raw = bytes(self.buffer).decode("ascii", errors="replace")
        self.buffer.clear()
        self.event.clear()
        # Trim everything from '>' onward; '>' is the prompt, not data.
        idx = raw.find(">")
        if idx >= 0:
            raw = raw[:idx]
        return raw


async def _send(client, framer: _Framer, write_uuid: str, cmd: str,
                timeout: float = 5.0) -> str:
    """Send a command, wait for the '>' prompt, return the cleaned response."""
    framer.buffer.clear()
    framer.event.clear()
    await client.write_gatt_char(write_uuid, f"{cmd}\r".encode("ascii"),
                                 response=False)
    try:
        await asyncio.wait_for(framer.event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    raw = framer.take()
    # Drop echo + blank lines
    lines = []
    for line in raw.replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s or s.upper() == cmd.upper():
            continue
        if s.upper().startswith("SEARCHING"):
            continue
        lines.append(s)
    return "\n".join(lines)


async def try_uuid_set(client, uuid_set: dict) -> bool:
    """Probe whether this UUID profile returns an 'ELM' banner on ATZ."""
    name = uuid_set["name"]
    notify_uuid = uuid_set["notify"]
    write_uuid = uuid_set["write"]

    try:
        if not client.services.get_characteristic(write_uuid):
            return False
        if not client.services.get_characteristic(notify_uuid):
            return False
    except Exception:  # noqa: BLE001
        return False

    print(f"  Trying: {name}")
    framer = _Framer()
    try:
        await client.start_notify(notify_uuid, framer.on_notify)
        await asyncio.sleep(0.3)
        resp = await _send(client, framer, write_uuid, "ATZ", timeout=3.0)
        await client.stop_notify(notify_uuid)
    except Exception as e:  # noqa: BLE001
        print(f"    ✗ Error: {e}")
        return False

    if resp and "ELM" in resp.upper():
        print(f"    ✓ ELM banner: {resp}")
        return True
    if resp:
        print(f"    ? Got response but no 'ELM' banner: {resp!r}")
    else:
        print("    ✗ No response")
    return False


async def interactive_session(client, write_uuid: str, notify_uuid: str) -> None:
    """Run a canned diagnostic sequence over BLE + print results."""
    print("\n=== OBD Diagnostic Session (BLE) ===\n")
    framer = _Framer()
    await client.start_notify(notify_uuid, framer.on_notify)

    # Initialize ELM327 — same sequence as the production connection class.
    init = [
        ("ATZ",    "Reset",           3.0),
        ("ATE0",   "Echo off",        2.0),
        ("ATL0",   "Linefeeds off",   2.0),
        ("ATS0",   "Spaces off",      2.0),
        ("ATH1",   "Headers on",      2.0),
        ("ATCAF1", "CAN auto-format", 2.0),
        ("ATAT1",  "Adaptive timing", 2.0),
        ("ATST64", "Response timeout 400ms", 2.0),
        ("ATSP0",  "Auto protocol",   2.0),
    ]
    for cmd, desc, timeout in init:
        print(f">>> {cmd}  ({desc})")
        resp = await _send(client, framer, write_uuid, cmd, timeout=timeout)
        for line in (resp or "(no response)").split("\n"):
            print(f"    {line}")
        print()

    # Diagnostic queries
    queries = [
        ("ATRV",   "Battery voltage",              2.0),
        ("ATDPN",  "Detected protocol (number)",   2.0),
        ("0100",   "Supported PIDs [01-20]",       10.0),
        ("010C",   "Engine RPM",                   5.0),
        ("010D",   "Vehicle speed",                5.0),
        ("0105",   "Coolant temp",                 5.0),
        ("03",     "Stored DTCs",                  5.0),
    ]
    for cmd, desc, timeout in queries:
        print(f">>> {cmd}  ({desc})")
        resp = await _send(client, framer, write_uuid, cmd, timeout=timeout)
        for line in (resp or "(no response)").split("\n"):
            print(f"    {line}")
        print()

    await client.stop_notify(notify_uuid)


async def main() -> None:
    from bleak import BleakClient

    parser = argparse.ArgumentParser(description="BLE OBD tester")
    parser.add_argument("--address", required=True, help="BLE device address/UUID")
    parser.add_argument("--write-uuid", default=None,
                        help="GATT write characteristic UUID")
    parser.add_argument("--notify-uuid", default=None,
                        help="GATT notify characteristic UUID")
    args = parser.parse_args()

    print(f"Connecting to {args.address}...")
    async with BleakClient(args.address, timeout=20.0) as client:
        print(f"Connected: {client.is_connected}\n")

        if args.write_uuid and args.notify_uuid:
            await interactive_session(client, args.write_uuid, args.notify_uuid)
            return

        print("Auto-detecting GATT UUIDs...\n")
        for uuid_set in KNOWN_UUID_SETS:
            if await try_uuid_set(client, uuid_set):
                print(f"\n  ✓ Using profile: {uuid_set['name']}")
                print(f"    Write:  {uuid_set['write']}")
                print(f"    Notify: {uuid_set['notify']}\n")
                await interactive_session(client, uuid_set["write"], uuid_set["notify"])
                return

        print("\nNo known UUID profile worked.")
        print("Run: python3 ble_scan.py --address", args.address)
        print("to discover the correct UUIDs manually.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except ImportError:
        print("ERROR: 'bleak' is not installed.")
        print("Install it with: pip install bleak")
        sys.exit(1)
