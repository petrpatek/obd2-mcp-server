"""
BLE OBD connection — communicates with ELM327-based OBD adapters
over Bluetooth Low Energy (GATT characteristics).

This is needed for adapters like the Vgate vLinker FD, which use BLE
instead of classic Bluetooth SPP. The /dev/tty serial port that macOS
creates for these adapters is non-functional — data must go through
BLE GATT read/write/notify characteristics.

Features:
  - Auto-discovery: scans for OBD adapters by name pattern
  - Auto UUID detection: tries known GATT characteristic sets
  - Retry with backoff: reconnects on BLE drops
  - Graceful degradation: server stays up even if BLE fails

Requires: pip install bleak
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from obd2_mcp.obd_connection import (
    DTC,
    FreezeFrame,
    Module,
    SensorReading,
    VehicleInfo,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known OBD adapter BLE name patterns (case-insensitive substring match)
# ---------------------------------------------------------------------------
OBD_NAME_PATTERNS = [
    "vlinker", "obdlink", "veepeak", "elm327", "elm329",
    "obd", "carista", "lelink", "konnwei", "ancel",
    "bluedriver", "fixd", "bafx",
]

# ---------------------------------------------------------------------------
# Known BLE GATT UUID sets for ELM327-based adapters
# ---------------------------------------------------------------------------
KNOWN_UUID_SETS = [
    {
        "name": "E7810A71 (vLinker FD, Veepeak)",
        "service": "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
        "notify": "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
        "write": "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
    },
    {
        "name": "18F0/2AF0/2AF1 (vLinker FD alternate)",
        "service": "000018f0-0000-1000-8000-00805f9b34fb",
        "notify": "00002af0-0000-1000-8000-00805f9b34fb",
        "write": "00002af1-0000-1000-8000-00805f9b34fb",
    },
    {
        "name": "FFF0 (OBDLink CX, generic)",
        "service": "0000fff0-0000-1000-8000-00805f9b34fb",
        "notify": "0000fff1-0000-1000-8000-00805f9b34fb",
        "write": "0000fff2-0000-1000-8000-00805f9b34fb",
    },
    {
        "name": "FFE0 (LeLink, generic Chinese)",
        "service": "0000ffe0-0000-1000-8000-00805f9b34fb",
        "notify": "0000ffe1-0000-1000-8000-00805f9b34fb",
        "write": "0000ffe1-0000-1000-8000-00805f9b34fb",
    },
]

# Known OBD-related BLE service UUIDs (for discovery fallback)
OBD_SERVICE_UUIDS = [s["service"] for s in KNOWN_UUID_SETS]


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

async def discover_obd_adapter(
    scan_duration: float = 10.0,
    name_hint: str | None = None,
) -> dict | None:
    """Scan for BLE OBD adapters and return the best match.

    Returns dict with 'address', 'name', 'rssi' or None if not found.
    """
    from bleak import BleakScanner

    logger.info("Scanning for BLE OBD adapters (%0.0fs)...", scan_duration)
    devices = await BleakScanner.discover(
        timeout=scan_duration,
        return_adv=True,
    )

    candidates = []
    for device, adv_data in devices.values():
        name = device.name or adv_data.local_name or ""
        name_lower = name.lower()

        # Match by name hint (exact substring)
        if name_hint and name_hint.lower() in name_lower:
            candidates.append({
                "address": device.address,
                "name": name,
                "rssi": adv_data.rssi,
                "match": "name_hint",
            })
            continue

        # Match by known OBD adapter name patterns
        if any(p in name_lower for p in OBD_NAME_PATTERNS):
            candidates.append({
                "address": device.address,
                "name": name,
                "rssi": adv_data.rssi,
                "match": "name_pattern",
            })
            continue

        # Match by advertised service UUID
        for svc_uuid in (adv_data.service_uuids or []):
            if svc_uuid.lower() in [u.lower() for u in OBD_SERVICE_UUIDS]:
                candidates.append({
                    "address": device.address,
                    "name": name or "(unnamed OBD adapter)",
                    "rssi": adv_data.rssi,
                    "match": "service_uuid",
                })
                break

    if not candidates:
        logger.warning("No BLE OBD adapters found in scan")
        return None

    # Sort: name_hint matches first, then by signal strength
    priority = {"name_hint": 0, "name_pattern": 1, "service_uuid": 2}
    candidates.sort(key=lambda c: (priority.get(c["match"], 9), -c["rssi"]))

    best = candidates[0]
    logger.info("Found OBD adapter: %s (%s) RSSI: %d dBm [matched by %s]",
                best["name"], best["address"], best["rssi"], best["match"])

    if len(candidates) > 1:
        logger.info("Also found %d other candidate(s): %s",
                     len(candidates) - 1,
                     ", ".join(f"{c['name']} ({c['address'][:8]}...)" for c in candidates[1:]))

    return best


# ---------------------------------------------------------------------------
# BLE OBD Connection
# ---------------------------------------------------------------------------

class BLEOBDConnection:
    """
    Connects to a BLE ELM327 OBD adapter using bleak.

    Supports:
      - Explicit address or auto-discovery
      - Auto UUID detection from known adapter profiles
      - Retry logic with exponential backoff
      - Lazy reconnection on command failure

    All protocol methods return coroutines so the MCP server's
    _call_method helper can await them directly.
    """

    MAX_RETRIES = 3
    RETRY_DELAYS = [2.0, 5.0, 10.0]  # Exponential backoff

    def __init__(
        self,
        address: str | None = None,
        write_uuid: str | None = None,
        notify_uuid: str | None = None,
        name_hint: str | None = None,
    ):
        try:
            import bleak  # noqa: F401
        except ImportError:
            raise ImportError(
                "bleak is required for BLE OBD connections. "
                "Install it with: pip install bleak"
            )

        self._address = address
        self._write_uuid = write_uuid
        self._notify_uuid = notify_uuid
        self._name_hint = name_hint
        self._client: Any = None
        self._connected = False
        self._adapter_name: str = ""
        self._response_buffer = bytearray()
        self._response_event = asyncio.Event()
        self._disconnect_event = asyncio.Event()

    async def connect(self):
        """Establish BLE connection with auto-discovery and retry logic."""
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                if attempt > 0:
                    delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                    logger.info("Retry %d/%d in %.0fs...", attempt + 1, self.MAX_RETRIES, delay)
                    await asyncio.sleep(delay)

                await self._connect_once()
                if self._connected:
                    return  # Success
                else:
                    last_error = RuntimeError("Connection established but initialization failed")

            except Exception as e:
                last_error = e
                logger.warning("BLE connection attempt %d failed: %s", attempt + 1, e)
                # Clean up partial connection
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None

        # All retries exhausted
        if last_error:
            raise ConnectionError(
                f"Failed to connect after {self.MAX_RETRIES} attempts: {last_error}\n"
                "Troubleshooting:\n"
                "  1. Is the car ignition ON? (adapter needs 12V from OBD port)\n"
                "  2. Unpair classic Bluetooth: System Settings → Bluetooth → "
                "Forget 'vLinkerFD-Android'\n"
                "  3. Is the adapter plugged into the OBD port?\n"
                "  4. Try: python3 ble_scan.py --connect"
            ) from last_error

    async def _connect_once(self):
        """Single connection attempt — discover, connect, initialize."""
        from bleak import BleakClient

        # Step 1: Resolve address (auto-discover if needed)
        address = self._address
        if not address or address.lower() == "auto":
            adapter = await discover_obd_adapter(
                scan_duration=10.0,
                name_hint=self._name_hint,
            )
            if not adapter:
                raise ConnectionError(
                    "No BLE OBD adapter found. Make sure ignition is ON "
                    "and the adapter is plugged in."
                )
            address = adapter["address"]
            self._adapter_name = adapter["name"]
        else:
            self._adapter_name = address[:12]

        # Step 2: Connect
        logger.info("Connecting to BLE OBD adapter: %s (%s)", self._adapter_name, address)
        self._client = BleakClient(
            address,
            timeout=20.0,
            disconnected_callback=self._on_disconnect,
        )
        await self._client.connect()

        if not self._client.is_connected:
            raise ConnectionError(f"BLE connect returned but device is not connected: {address}")

        logger.info("BLE connected to %s", self._adapter_name)

        # Step 3: Detect GATT UUIDs
        if not self._write_uuid or not self._notify_uuid:
            await self._detect_uuids()

        if not self._write_uuid or not self._notify_uuid:
            raise ConnectionError(
                "Could not find OBD GATT characteristics on this device. "
                "Try specifying --ble-write-uuid and --ble-notify-uuid manually."
            )

        # Step 4: Subscribe to notifications
        await self._client.start_notify(self._notify_uuid, self._on_notify)

        # Step 5: Initialize ELM327
        resp = await self._send_command("ATZ", timeout=3.0)
        if not resp or "ELM" not in resp.upper():
            # Try once more — some adapters need a double reset
            await asyncio.sleep(0.5)
            resp = await self._send_command("ATZ", timeout=3.0)

        if resp:
            logger.info("ELM327 identified: %s", resp.strip())
        else:
            logger.warning("No response to ATZ — adapter may not be powered")

        await self._send_command("ATE0", timeout=2.0)   # Echo off
        await self._send_command("ATL0", timeout=1.0)   # Linefeeds off
        await self._send_command("ATS0", timeout=1.0)   # Spaces off
        await self._send_command("ATH0", timeout=1.0)   # Headers off
        await self._send_command("ATSP0", timeout=5.0)  # Auto-detect protocol

        self._connected = True
        self._disconnect_event.clear()
        logger.info("BLE OBD adapter ready: %s", self._adapter_name)

    def _on_disconnect(self, client):
        """Called by bleak when the BLE connection drops."""
        logger.warning("BLE disconnected from %s", self._adapter_name)
        self._connected = False
        self._disconnect_event.set()

    async def _reconnect(self):
        """Attempt to reconnect after a disconnect."""
        logger.info("Attempting BLE reconnect...")
        self._connected = False
        try:
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
            self._client = None
            # Reset UUIDs so they get re-detected (device may have changed)
            saved_write = self._write_uuid
            saved_notify = self._notify_uuid
            await self._connect_once()
            if not self._connected:
                # Restore UUIDs for next attempt
                self._write_uuid = saved_write
                self._notify_uuid = saved_notify
        except Exception as e:
            logger.error("Reconnect failed: %s", e)

    async def _detect_uuids(self):
        """Try known UUID sets, then fallback to scanning all characteristics."""
        for uuid_set in KNOWN_UUID_SETS:
            try:
                write_char = self._client.services.get_characteristic(uuid_set["write"])
                notify_char = self._client.services.get_characteristic(uuid_set["notify"])
                if write_char and notify_char:
                    self._write_uuid = uuid_set["write"]
                    self._notify_uuid = uuid_set["notify"]
                    logger.info("Using UUID set: %s", uuid_set["name"])
                    return
            except Exception:
                continue

        # Fallback: find any writable + notifiable characteristics
        for service in self._client.services:
            for char in service.characteristics:
                if "write" in char.properties or "write-without-response" in char.properties:
                    if not self._write_uuid:
                        self._write_uuid = char.uuid
                if "notify" in char.properties:
                    if not self._notify_uuid:
                        self._notify_uuid = char.uuid

        if self._write_uuid and self._notify_uuid:
            logger.info("Auto-detected UUIDs — write: %s, notify: %s",
                        self._write_uuid, self._notify_uuid)

    def _on_notify(self, sender, data: bytearray):
        """Handle incoming BLE GATT notifications."""
        self._response_buffer.extend(data)
        if b">" in data:
            self._response_event.set()

    async def _send_command(self, cmd: str, timeout: float = 5.0) -> str:
        """Send an AT/OBD command and wait for the '>' prompt response.

        Automatically attempts reconnection if the BLE link is down.
        """
        if not self._connected or not self._client or not self._client.is_connected:
            await self._reconnect()
            if not self._connected:
                return ""

        self._response_buffer.clear()
        self._response_event.clear()

        try:
            await self._client.write_gatt_char(
                self._write_uuid,
                f"{cmd}\r".encode(),
                response=False,
            )
        except Exception as e:
            logger.warning("BLE write failed (%s), reconnecting...", e)
            await self._reconnect()
            if not self._connected:
                return ""
            # Retry the write once
            try:
                self._response_buffer.clear()
                self._response_event.clear()
                await self._client.write_gatt_char(
                    self._write_uuid,
                    f"{cmd}\r".encode(),
                    response=False,
                )
            except Exception as e2:
                logger.error("BLE write failed after reconnect: %s", e2)
                return ""

        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug("Timeout waiting for response to: %s", cmd)

        raw = bytes(self._response_buffer).decode("ascii", errors="replace")
        lines = [l.strip() for l in raw.replace("\r", "\n").split("\n")
                 if l.strip() and l.strip() != ">" and l.strip() != cmd]
        result = "\n".join(lines)
        logger.debug("CMD: %s -> %s", cmd, result)
        return result

    # -------------------------------------------------------------------
    # Protocol methods — all async
    # -------------------------------------------------------------------

    def is_connected(self) -> bool:
        return self._connected

    async def get_dtcs(self, module: str | None = None) -> list[DTC]:
        response = await self._send_command("03")

        dtcs: list[DTC] = []
        if not response or "NO DATA" in response or "ERROR" in response:
            return dtcs

        hex_data = response.replace(" ", "").replace("\n", "")
        if hex_data.startswith("43"):
            hex_data = hex_data[2:]

        for i in range(0, len(hex_data) - 3, 4):
            try:
                byte1 = int(hex_data[i:i+2], 16)
                byte2 = int(hex_data[i+2:i+4], 16)
                if byte1 == 0 and byte2 == 0:
                    continue
                code = self._decode_dtc(byte1, byte2)
                from obd2_mcp.dtc_database import lookup_dtc
                desc = lookup_dtc(code)
                dtcs.append(DTC(code=code, description=desc))
            except (ValueError, IndexError):
                continue

        return dtcs

    def _decode_dtc(self, byte1: int, byte2: int) -> str:
        """Decode two bytes into a DTC string like P0420."""
        prefixes = {0: "P", 1: "C", 2: "B", 3: "U"}
        first_digit = (byte1 >> 6) & 0x03
        second_digit = (byte1 >> 4) & 0x03
        rest = f"{byte1 & 0x0F:01X}{byte2:02X}"
        return f"{prefixes[first_digit]}{second_digit}{rest}"

    async def clear_dtcs(self, module: str | None = None) -> bool:
        response = await self._send_command("04")
        return "44" in response or "OK" in response.upper()

    async def get_live_data(self, pids: list[str] | None = None) -> list[SensorReading]:
        readings: list[SensorReading] = []

        pid_map = {
            "RPM": ("010C", 2, lambda b: ((b[0] << 8) | b[1]) / 4, "rpm"),
            "SPEED": ("010D", 1, lambda b: b[0], "km/h"),
            "COOLANT_TEMP": ("0105", 1, lambda b: b[0] - 40, "\u00b0C"),
            "ENGINE_LOAD": ("0104", 1, lambda b: b[0] * 100 / 255, "%"),
            "INTAKE_TEMP": ("010F", 1, lambda b: b[0] - 40, "\u00b0C"),
            "THROTTLE_POS": ("0111", 1, lambda b: b[0] * 100 / 255, "%"),
            "TIMING_ADVANCE": ("010E", 1, lambda b: b[0] / 2 - 64, "\u00b0"),
            "MAF": ("0110", 2, lambda b: ((b[0] << 8) | b[1]) / 100, "g/s"),
            "FUEL_PRESSURE": ("010A", 1, lambda b: b[0] * 3, "kPa"),
            "SHORT_FUEL_TRIM_1": ("0106", 1, lambda b: (b[0] - 128) * 100 / 128, "%"),
            "LONG_FUEL_TRIM_1": ("0107", 1, lambda b: (b[0] - 128) * 100 / 128, "%"),
            "CONTROL_MODULE_VOLTAGE": ("0142", 2, lambda b: ((b[0] << 8) | b[1]) / 1000, "V"),
        }

        target_pids = pids if pids else list(pid_map.keys())

        for pid_name in target_pids:
            key = pid_name.upper()
            if key not in pid_map:
                continue

            obd_cmd, num_bytes, converter, unit = pid_map[key]
            response = await self._send_command(obd_cmd, timeout=3.0)

            if not response or "NO DATA" in response or "ERROR" in response:
                continue

            try:
                hex_clean = response.replace(" ", "").replace("\n", "")
                header = "41" + obd_cmd[2:]
                idx = hex_clean.find(header)
                if idx < 0:
                    continue
                data_hex = hex_clean[idx + len(header):]

                data_bytes = []
                for bi in range(num_bytes):
                    data_bytes.append(int(data_hex[bi*2:bi*2+2], 16))

                converted = converter(data_bytes)
                readings.append(SensorReading(
                    pid=key,
                    name=key.replace("_", " ").title(),
                    value=round(converted, 2),
                    unit=unit,
                ))
            except (ValueError, IndexError) as e:
                logger.debug("Failed to parse %s: %s (raw: %s)", key, e, response)

        return readings

    async def get_vehicle_info(self) -> VehicleInfo:
        info = VehicleInfo()

        # VIN (Mode 09, PID 02)
        response = await self._send_command("0902", timeout=5.0)
        if response and "NO DATA" not in response:
            hex_clean = response.replace(" ", "").replace("\n", "")
            vin_hex = re.sub(r"4902\d{2}", "", hex_clean)
            try:
                vin_bytes = bytes.fromhex(vin_hex)
                info.vin = vin_bytes.decode("ascii", errors="replace").strip("\x00").strip()
            except ValueError:
                pass

        # Protocol
        proto_response = await self._send_command("ATDPN", timeout=2.0)
        if proto_response:
            info.obd_standard = proto_response.strip()

        # Battery voltage
        bat_response = await self._send_command("ATRV", timeout=2.0)
        if bat_response:
            info.ecu_name = f"Battery: {bat_response.strip()}"

        return info

    async def list_modules(self) -> list[Module]:
        modules: list[Module] = []

        response = await self._send_command("0100", timeout=5.0)
        if response and "NO DATA" not in response:
            proto = await self._send_command("ATDPN", timeout=2.0)
            modules.append(Module(
                address="0x7E0",
                name="Engine Control Module (ECM)",
                protocol=proto.strip() if proto else "auto",
            ))

        return modules

    async def get_freeze_frame(self, dtc_code: str) -> FreezeFrame | None:
        response = await self._send_command("0200", timeout=5.0)
        if not response or "NO DATA" in response:
            return None
        return FreezeFrame(dtc_code=dtc_code)

    def close(self) -> None:
        if self._client and self._client.is_connected:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(self._client.disconnect())
            except Exception:
                pass
        self._connected = False
        logger.info("BLE OBD connection closed")
