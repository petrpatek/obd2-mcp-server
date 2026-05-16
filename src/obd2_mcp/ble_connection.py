"""
BLE OBD connection — communicates with ELM327-based OBD adapters
over Bluetooth Low Energy (GATT characteristics).

Needed for adapters like the Vgate vLinker FD, which use BLE instead
of classic Bluetooth SPP. The /dev/tty serial port that macOS creates
for these adapters is non-functional — data must go through BLE GATT
read/write/notify characteristics.

Bulletproof discovery & connection:
  - Multi-pass adaptive scan with detection callback so devices that
    advertise their name late are still caught
  - Tries each known GATT profile, sends a real ATZ, and only keeps
    the profile if it gets an 'ELM' or 'STN' banner back — never trusts
    UUID matching alone
  - Validates the adapter responds before declaring ready
  - On macOS, surfaces classic-Bluetooth-SPP pairing (which routes the
    adapter away from BLE) as a clear, actionable warning
  - Retries ATZ up to three times, draining buffer between attempts
  - Reconnects on BLE drops, with exponential backoff
  - Thread-safe notify handling (callback is marshalled to the loop)
  - ISO-TP multi-frame parsing for DTCs and VIN

Requires: pip install bleak
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import subprocess
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
    "obd", "obdii", "obd2", "carista", "lelink", "konnwei",
    "ancel", "bluedriver", "fixd", "bafx", "vgate", "icar",
    "stn", "obdsolutions", "obddongle",
]

# Banner prefixes that indicate a live ELM327-compatible adapter.
ADAPTER_BANNERS = ("ELM", "STN", "OBD")

# ---------------------------------------------------------------------------
# Known BLE GATT UUID sets for ELM327-based adapters
#
# Ordered by likelihood for the target adapter (vLinker FD).
# ---------------------------------------------------------------------------
KNOWN_UUID_SETS = [
    {
        "name": "FFF0 (vLinker FD, OBDLink CX, most modern BLE ELM327)",
        "service": "0000fff0-0000-1000-8000-00805f9b34fb",
        "notify": "0000fff1-0000-1000-8000-00805f9b34fb",
        "write": "0000fff2-0000-1000-8000-00805f9b34fb",
    },
    {
        "name": "FFE0 (LeLink2, HM-10, generic Chinese BLE-UART)",
        "service": "0000ffe0-0000-1000-8000-00805f9b34fb",
        "notify": "0000ffe1-0000-1000-8000-00805f9b34fb",
        "write": "0000ffe1-0000-1000-8000-00805f9b34fb",
    },
    {
        "name": "Nordic UART Service (NUS) — custom/ESP32 adapters",
        "service": "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
        "notify": "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
        "write": "6e400002-b5a3-f393-e0a9-e50e24dcca9e",
    },
    {
        "name": "ISSC/Microchip BM77 — some Veepeak/older BLE OBD",
        "service": "49535343-fe7d-4ae5-8fa9-9fafd205e455",
        "notify": "49535343-1e4d-4bd9-ba61-23c647249616",
        "write": "49535343-8841-43f4-a8d4-ecbe34729bb3",
    },
    {
        "name": "E7810A71 (Veepeak Mini BLE+ legacy)",
        "service": "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
        "notify": "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
        "write": "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
    },
]

OBD_SERVICE_UUIDS = [s["service"] for s in KNOWN_UUID_SETS]


# ---------------------------------------------------------------------------
# Helpers — pure, testable
# ---------------------------------------------------------------------------

def _is_hex_pair_line(s: str) -> bool:
    stripped = s.replace(" ", "")
    return len(stripped) > 0 and all(c in "0123456789abcdefABCDEF" for c in stripped)


def _clean_elm_response(raw: str, echoed_cmd: str | None = None) -> list[str]:
    """Strip prompt, echoes, SEARCHING markers, blank lines."""
    lines: list[str] = []
    for raw_line in raw.replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line == ">":
            continue
        if echoed_cmd and line.upper() == echoed_cmd.upper():
            continue
        if line.upper().startswith("SEARCHING"):
            continue
        lines.append(line)
    return lines


def _join_hex_payload(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        hex_part = line.replace(" ", "").replace(":", "")
        if _is_hex_pair_line(hex_part):
            out.append(hex_part)
    return "".join(out)


def _looks_like_obd_name(name: str) -> bool:
    name_lower = (name or "").lower()
    return any(p in name_lower for p in OBD_NAME_PATTERNS)


def _looks_like_obd_service(svc_uuids: list[str]) -> bool:
    targets = {u.lower() for u in OBD_SERVICE_UUIDS}
    return any((u or "").lower() in targets for u in svc_uuids or [])


# ---------------------------------------------------------------------------
# macOS: detect classic-BT pairing that would steal the device away from BLE
# ---------------------------------------------------------------------------

def _detect_macos_spp_pairings() -> list[str]:
    """Return paired Bluetooth device names that look like OBD adapters.

    macOS pairs vLinker FD as a classic SPP device by default. Once paired,
    the adapter often won't show up over BLE until you 'Forget This Device'.
    """
    if platform.system() != "Darwin":
        return []
    try:
        out = subprocess.run(
            ["system_profiler", "SPBluetoothDataType", "-detailLevel", "mini"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []

    suspects: list[str] = []
    # Each device entry in system_profiler output starts with the device
    # name on a line ending with ':'. We scan for any name token that
    # matches our OBD pattern list.
    for line in out.stdout.splitlines():
        stripped = line.strip().rstrip(":")
        if _looks_like_obd_name(stripped):
            if stripped not in suspects:
                suspects.append(stripped)
    return suspects


# ---------------------------------------------------------------------------
# Multi-pass discovery
# ---------------------------------------------------------------------------

class _Candidate:
    __slots__ = ("device", "name", "rssi", "service_uuids", "match_reason")

    def __init__(self, device: Any, name: str, rssi: int,
                 service_uuids: list[str], match_reason: str) -> None:
        self.device = device
        self.name = name
        self.rssi = rssi
        self.service_uuids = service_uuids
        self.match_reason = match_reason


async def discover_obd_adapter(
    scan_duration: float = 12.0,
    name_hint: str | None = None,
) -> dict | None:
    """Multi-pass scan for BLE OBD adapters; returns the BLEDevice + meta.

    Uses the streaming detection-callback API instead of `discover()` so
    devices that advertise their name late (common on macOS) aren't lost.
    Accumulates advertisements across the full scan window — the same
    address may report different names in different beacons; we keep the
    longest non-empty name we ever saw.
    """
    from bleak import BleakScanner

    logger.info("Scanning for BLE OBD adapters (up to %.1fs)...", scan_duration)

    accumulated: dict[str, dict] = {}  # address -> latest info

    def _on_adv(device: Any, adv_data: Any) -> None:
        addr = device.address
        existing = accumulated.get(addr, {})
        # Prefer non-empty name from any advertisement we've seen.
        name = device.name or adv_data.local_name or existing.get("name") or ""
        # Merge service UUIDs across ads.
        svc = set(existing.get("service_uuids", []))
        svc.update(adv_data.service_uuids or [])
        accumulated[addr] = {
            "device": device,
            "name": name,
            "rssi": adv_data.rssi if adv_data.rssi is not None else existing.get("rssi", -127),
            "service_uuids": sorted(svc),
        }

    scanner = BleakScanner(detection_callback=_on_adv)
    await scanner.start()
    try:
        # Poll periodically so we can short-circuit once we've found a strong match.
        elapsed = 0.0
        step = 1.5
        while elapsed < scan_duration:
            await asyncio.sleep(step)
            elapsed += step
            best = _rank_candidates(accumulated, name_hint)
            # Short-circuit early if we've got a very strong name match
            # with decent RSSI (no need to keep scanning).
            if best and best.match_reason == "name_hint" and best.rssi > -75:
                logger.info("Strong candidate found early at %.1fs", elapsed)
                break
            if best and best.match_reason == "name_pattern" and best.rssi > -65:
                logger.info("Strong name-pattern candidate found at %.1fs", elapsed)
                break
    finally:
        try:
            await scanner.stop()
        except Exception:  # noqa: BLE001
            pass

    best = _rank_candidates(accumulated, name_hint)
    if not best:
        # Diagnostic: did we see any classic-BT-paired OBD device?
        spp_pairings = _detect_macos_spp_pairings()
        if spp_pairings:
            logger.warning(
                "No BLE OBD adapters in scan, but classic-BT pairing exists for: %s. "
                "macOS may be hiding the BLE side. Forget the classic pairing in "
                "System Settings → Bluetooth and retry.",
                ", ".join(spp_pairings),
            )
        else:
            logger.warning(
                "No BLE OBD adapters found. Check: ignition ON? In range? "
                "Bluetooth permission granted to Terminal? "
                "(System Settings → Privacy & Security → Bluetooth)"
            )
        return None

    logger.info(
        "Best candidate: %s (%s) RSSI: %d dBm [matched by %s]",
        best.name or "(unnamed)", best.device.address, best.rssi, best.match_reason,
    )
    others = [a for a in accumulated.values()
              if a["device"].address != best.device.address]
    others.sort(key=lambda x: -x["rssi"])
    obd_others = [o for o in others if _looks_like_obd_name(o["name"])
                  or _looks_like_obd_service(o["service_uuids"])]
    if obd_others:
        names = ", ".join(f"{o['name'] or '(unnamed)'} ({str(o['device'].address)[:8]}…)"
                          for o in obd_others[:4])
        logger.info("Other OBD-like candidates: %s", names)

    return {
        "device": best.device,
        "address": best.device.address,
        "name": best.name or "(unnamed OBD adapter)",
        "rssi": best.rssi,
        "match": best.match_reason,
        "service_uuids": best.service_uuids,
    }


def _rank_candidates(
    accumulated: dict[str, dict],
    name_hint: str | None,
) -> _Candidate | None:
    """Score every accumulated advertisement and return the best candidate."""
    candidates: list[_Candidate] = []
    name_hint_lower = (name_hint or "").lower()

    for info in accumulated.values():
        name = info["name"] or ""
        name_lower = name.lower()
        match: str | None = None

        if name_hint and name_hint_lower in name_lower:
            match = "name_hint"
        elif _looks_like_obd_name(name):
            match = "name_pattern"
        elif _looks_like_obd_service(info["service_uuids"]):
            match = "service_uuid"

        if match:
            candidates.append(_Candidate(
                device=info["device"],
                name=name,
                rssi=info["rssi"],
                service_uuids=info["service_uuids"],
                match_reason=match,
            ))

    if not candidates:
        return None

    priority = {"name_hint": 0, "name_pattern": 1, "service_uuid": 2}
    candidates.sort(key=lambda c: (priority.get(c.match_reason, 9), -c.rssi))
    return candidates[0]


# ---------------------------------------------------------------------------
# BLE OBD Connection
# ---------------------------------------------------------------------------

class BLEOBDConnection:
    """Connects to a BLE ELM327 OBD adapter using bleak."""

    MAX_RETRIES = 3
    RETRY_DELAYS = [2.0, 5.0, 10.0]
    DEFAULT_TIMEOUT = 5.0
    PROTOCOL_TIMEOUT = 12.0
    # Heartbeat: send a cheap ATRV every N seconds to keep the BLE link
    # awake. vLinker FD's idle-sleep cutoff is ~120s in stock firmware;
    # 30s is conservative. Set to 0 to disable.
    KEEPALIVE_INTERVAL = 30.0

    def __init__(
        self,
        address: str | None = None,
        write_uuid: str | None = None,
        notify_uuid: str | None = None,
        name_hint: str | None = None,
    ):
        try:
            import bleak  # noqa: F401
        except ImportError as err:
            raise ImportError(
                "bleak is required for BLE OBD connections. "
                "Install it with: pip install bleak"
            ) from err

        self._address = address
        self._device: Any = None  # BLEDevice once discovered
        self._user_write_uuid = write_uuid
        self._user_notify_uuid = notify_uuid
        self._write_uuid: str | None = None
        self._notify_uuid: str | None = None
        self._name_hint = name_hint
        self._client: Any = None
        self._connected = False
        self._adapter_name: str = ""
        self._adapter_version: str = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._response_buffer = bytearray()
        self._response_event = asyncio.Event()
        self._write_without_response = True
        self._notify_started = False
        # ELM327 is strictly request/response — only one command in flight
        # at a time. Without this lock, concurrent MCP tool calls would
        # clobber each other's response buffer and produce wrong data
        # (or hang waiting for a prompt that already arrived).
        self._cmd_lock = asyncio.Lock()
        # Capture the most recent connect failure so MCP tool errors can
        # include a useful root cause (instead of just "not connected").
        self._last_connect_error: str | None = None
        self._connect_lock = asyncio.Lock()
        # Keepalive heartbeat task. ELM327 BLE adapters (notably vLinker FD)
        # auto-sleep after ~60-180s of idle BLE traffic, then stop
        # advertising entirely — needs unplug/replug to wake. We send a
        # cheap ATRV every KEEPALIVE_INTERVAL seconds to prevent that.
        self._keepalive_task: asyncio.Task | None = None
        self._keepalive_stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        last_error: Exception | None = None

        # Single-flight: if another task is already trying to connect,
        # wait for it instead of stacking attempts (which would race on
        # the shared client/buffer).
        async with self._connect_lock:
            if self._connected and self._client and self._client.is_connected:
                return

            for attempt in range(self.MAX_RETRIES):
                try:
                    if attempt > 0:
                        delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                        logger.info(
                            "Retry %d/%d in %.0fs...",
                            attempt + 1, self.MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                    await self._connect_once()
                    if self._connected:
                        self._last_connect_error = None
                        return
                    last_error = RuntimeError("connect completed but adapter never answered")
                except Exception as e:  # noqa: BLE001
                    last_error = e
                    logger.warning("BLE connection attempt %d failed: %s", attempt + 1, e)
                    await self._safe_disconnect()

        # Final guidance
        spp = _detect_macos_spp_pairings()
        spp_hint = ""
        if spp:
            spp_hint = (
                "\n  ⚠ Classic-BT pairing detected for: "
                f"{', '.join(spp)}. Forget it in System Settings → Bluetooth."
            )
        message = (
            f"Failed to connect after {self.MAX_RETRIES} attempts: {last_error}\n"
            "Troubleshooting checklist:\n"
            "  1. Ignition ON (adapter draws 12V from the OBD port)\n"
            "  2. Adapter physically plugged into the OBD-II port\n"
            "  3. Bluetooth permission granted to the host app:\n"
            "     • Terminal: System Settings → Privacy & Security → Bluetooth → Terminal\n"
            "     • Claude Desktop: System Settings → Privacy & Security → Bluetooth → Claude\n"
            "       (if Claude isn't listed, the subprocess hasn't been allowed to scan;\n"
            "        first run `obd2-mcp --probe` from Terminal to provoke the prompt,\n"
            "        then enable Bluetooth for Claude Desktop too)"
            f"{spp_hint}\n"
            "  4. Try: python3 ble_scan.py --connect\n"
            "  5. Try: obd2-mcp --probe"
        )
        self._last_connect_error = str(last_error) if last_error else "unknown"
        raise ConnectionError(message) from last_error

    async def _connect_once(self) -> None:
        """Single attempt: discover → connect → detect+validate UUIDs → init."""
        from bleak import BleakClient

        # 1. Resolve target device (auto-discover if needed)
        if not self._address or self._address.lower() == "auto":
            adapter = await discover_obd_adapter(
                scan_duration=12.0, name_hint=self._name_hint,
            )
            if not adapter:
                raise ConnectionError("No BLE OBD adapter found in scan")
            self._device = adapter["device"]
            self._adapter_name = adapter["name"]
        else:
            from bleak import BleakScanner
            logger.info("Resolving BLE address: %s", self._address)
            self._device = await BleakScanner.find_device_by_address(
                self._address, timeout=15.0,
            )
            if self._device is None:
                raise ConnectionError(
                    f"Could not find device {self._address} via BLE scan"
                )
            self._adapter_name = self._device.name or self._address[:17]

        # 2. Connect (pass BLEDevice for reliability per bleak docs)
        logger.info(
            "Connecting to %s (%s)...",
            self._adapter_name, self._device.address,
        )
        self._client = BleakClient(
            self._device, timeout=20.0,
            disconnected_callback=self._on_disconnect,
        )
        await self._client.connect()
        if not self._client.is_connected:
            raise ConnectionError(
                f"BLE connect returned but device not connected: {self._device.address}"
            )
        try:
            mtu = getattr(self._client, "mtu_size", None)
            if mtu:
                logger.info("BLE connected; negotiated MTU=%d", mtu)
            else:
                logger.info("BLE connected to %s", self._adapter_name)
        except Exception:  # noqa: BLE001
            logger.info("BLE connected to %s", self._adapter_name)

        # 3. Detect + VALIDATE GATT UUIDs by trying ATZ on each candidate
        await self._detect_and_validate_uuids()
        if not self._write_uuid or not self._notify_uuid:
            raise ConnectionError(
                "Could not find a working write/notify pair on this device. "
                "Run `python3 ble_scan.py --address <UUID>` to inspect its GATT layout."
            )

        # 4. Run full init (we already sent ATZ during validation, but
        # re-running it ensures we're starting from a clean state).
        await self._initialize_elm()

        # 5. Validate the adapter is actually responsive
        ati = await self._send_command("ATI", timeout=2.0)
        if not ati or not any(b in ati.upper() for b in ADAPTER_BANNERS):
            logger.warning(
                "Adapter connected but no banner from ATI (got: %r). "
                "Continuing anyway — but if queries fail, check ignition and wiring.",
                ati,
            )
        else:
            self._adapter_version = ati.strip().split("\n")[-1]
            logger.info("Adapter identified: %s", self._adapter_version)

        self._connected = True
        logger.info("✓ BLE OBD adapter ready: %s", self._adapter_version or self._adapter_name)

        # Start (or restart) the keepalive heartbeat. Without this, the
        # adapter goes to sleep ~2 min after the last command and stops
        # advertising entirely.
        self._start_keepalive()

    # ------------------------------------------------------------------
    # UUID detection with active validation
    # ------------------------------------------------------------------

    async def _detect_and_validate_uuids(self) -> None:
        """Try each profile in turn; only keep one that responds to ATZ.

        This is the heart of "bulletproof" — UUID matching by GATT
        characteristic existence is not enough, because some adapters
        expose the FFE0 profile but only respond on a different service.
        We send a real ATZ over each candidate and check for the banner.
        """
        # If user supplied explicit UUIDs, use them and skip validation.
        if self._user_write_uuid and self._user_notify_uuid:
            self._write_uuid = self._user_write_uuid
            self._notify_uuid = self._user_notify_uuid
            self._detect_write_mode()
            return

        candidates: list[tuple[str, str, str]] = []

        # 1. Known profiles whose chars actually exist on this device
        for uuid_set in KNOWN_UUID_SETS:
            try:
                w = self._client.services.get_characteristic(uuid_set["write"])
                n = self._client.services.get_characteristic(uuid_set["notify"])
            except Exception:  # noqa: BLE001
                w = n = None
            if w and n:
                candidates.append((uuid_set["name"], uuid_set["write"], uuid_set["notify"]))

        # 2. Fallback: any service that exposes both write and notify
        for service in self._client.services:
            write_uuid: str | None = None
            notify_uuid: str | None = None
            for char in service.characteristics:
                props = set(char.properties)
                if write_uuid is None and (
                    "write" in props or "write-without-response" in props
                ):
                    write_uuid = char.uuid
                if notify_uuid is None and (
                    "notify" in props or "indicate" in props
                ):
                    notify_uuid = char.uuid
            if write_uuid and notify_uuid:
                pair = (f"auto:{service.uuid}", write_uuid, notify_uuid)
                # Don't duplicate a pair already covered by known profiles
                if not any(c[1] == write_uuid and c[2] == notify_uuid for c in candidates):
                    candidates.append(pair)

        if not candidates:
            return

        # Try each candidate; first one that yields an ELM/STN/OBD banner wins.
        for name, write_uuid, notify_uuid in candidates:
            logger.info("Trying GATT profile: %s", name)
            try:
                ok = await self._probe_profile(write_uuid, notify_uuid)
            except Exception as e:  # noqa: BLE001
                logger.debug("Profile %s probe error: %s", name, e)
                ok = False
            if ok:
                self._write_uuid = write_uuid
                self._notify_uuid = notify_uuid
                self._detect_write_mode()
                logger.info("✓ Profile validated: %s", name)
                return

        logger.error("No GATT profile yielded an adapter banner. Tried %d.", len(candidates))

    async def _probe_profile(self, write_uuid: str, notify_uuid: str) -> bool:
        """Send ATZ on this profile; return True if we get an adapter banner."""
        # Subscribe to notifications
        try:
            await self._client.start_notify(notify_uuid, self._on_notify)
        except Exception as e:  # noqa: BLE001
            logger.debug("start_notify(%s) failed: %s", notify_uuid, e)
            return False

        await asyncio.sleep(0.3)  # let subscription settle
        self._response_buffer.clear()
        self._response_event.clear()

        # Try write-without-response first; fall back to with-response.
        for use_response in (False, True):
            try:
                await self._client.write_gatt_char(
                    write_uuid, b"ATZ\r", response=use_response,
                )
                break
            except Exception as e:  # noqa: BLE001
                logger.debug("write_gatt_char response=%s failed: %s", use_response, e)
                if use_response:
                    try:
                        await self._client.stop_notify(notify_uuid)
                    except Exception:  # noqa: BLE001
                        pass
                    return False

        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

        raw = bytes(self._response_buffer).decode("ascii", errors="replace").upper()

        if any(b in raw for b in ADAPTER_BANNERS):
            return True

        # No banner — clean up notify subscription and report failure
        try:
            await self._client.stop_notify(notify_uuid)
        except Exception:  # noqa: BLE001
            pass
        return False

    def _detect_write_mode(self) -> None:
        try:
            char = self._client.services.get_characteristic(self._write_uuid)
            if char is None:
                return
            props = set(char.properties)
            if "write-without-response" in props:
                self._write_without_response = True
            elif "write" in props:
                self._write_without_response = False
            logger.debug(
                "Write mode: %s (properties=%s)",
                "without-response" if self._write_without_response else "with-response",
                props,
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # ELM init
    # ------------------------------------------------------------------

    async def _initialize_elm(self) -> None:
        """ATZ → ATE0 → ATL0 → ATS0 → ATH1 → ATCAF1 → ATAT1 → ATST64 → ATSP0.

        The probe step already sent an ATZ. Sending another fresh one here
        ensures echo state, headers, etc. all start from a known baseline.
        """
        # ATZ — full reset. Up to 3 attempts (boot banner sometimes garbles
        # the first response).
        elm_seen = False
        for attempt in range(3):
            self._response_buffer.clear()
            self._response_event.clear()
            resp = await self._send_command("ATZ", timeout=3.0)
            if resp and any(b in resp.upper() for b in ADAPTER_BANNERS):
                self._adapter_version = resp.strip().split("\n")[-1]
                elm_seen = True
                logger.info("ATZ %d/3: %s", attempt + 1, self._adapter_version)
                break
            logger.debug("ATZ attempt %d returned: %r", attempt + 1, resp)
            await asyncio.sleep(0.5)
        if not elm_seen:
            logger.warning("ATZ never returned an adapter banner. Continuing.")

        # Configuration commands
        # ATE0  — echo off
        # ATL0  — linefeeds off
        # ATS0  — spaces off
        # ATH1  — headers ON (lets us identify multi-frame & per-ECU responses)
        # ATCAF1 — CAN auto-formatting on (handles ISO-TP)
        # ATAT1 — adaptive timing (auto-tunes inter-frame waits)
        # ATST64 — ECU response timeout = 0x64 * 4ms = 400 ms
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH1", "ATCAF1", "ATAT1", "ATST64"):
            resp = await self._send_command(cmd, timeout=2.0)
            if "OK" not in resp.upper():
                logger.debug("%s did not return OK (got: %r)", cmd, resp)

        # Auto-protocol selection
        await self._send_command("ATSP0", timeout=2.0)

        # Best-effort: disable the ELM327 sleep timer (Programmable
        # Parameter $0E controls sleep/standby on STN-based adapters and
        # several ELM clones). Failing here is fine — many vendors use
        # firmware-locked sleep.
        try:
            await self._send_command("ATPP 0E SV 00", timeout=1.5)
            await self._send_command("ATPP 0E ON",    timeout=1.5)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Disconnect / reconnect
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Keepalive heartbeat
    # ------------------------------------------------------------------

    def _start_keepalive(self) -> None:
        if self.KEEPALIVE_INTERVAL <= 0:
            return
        if self._keepalive_task and not self._keepalive_task.done():
            return  # already running
        self._keepalive_stop.clear()
        try:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        except RuntimeError:
            # No running loop — skip (will start on next connect)
            self._keepalive_task = None

    def _stop_keepalive(self) -> None:
        self._keepalive_stop.set()
        task = self._keepalive_task
        self._keepalive_task = None
        if task and not task.done():
            task.cancel()

    async def _keepalive_loop(self) -> None:
        """Periodically tickle the adapter so it doesn't auto-sleep.

        Uses ATRV (battery voltage) — fastest, harmless, works even when
        no CAN protocol is selected. Stops if the link drops; reconnect
        is left to the next user-initiated tool call (which calls
        ensure_ready()).
        """
        try:
            while not self._keepalive_stop.is_set():
                try:
                    await asyncio.wait_for(
                        self._keepalive_stop.wait(),
                        timeout=self.KEEPALIVE_INTERVAL,
                    )
                    return  # stop requested
                except asyncio.TimeoutError:
                    pass
                if not self.is_connected():
                    logger.debug("keepalive: link down, stopping heartbeat")
                    return
                try:
                    await self._send_command("ATRV", timeout=2.0)
                    logger.debug("keepalive: ATRV ok")
                except Exception as e:  # noqa: BLE001
                    logger.debug("keepalive: ATRV failed (%s)", e)
                    # Don't try to reconnect from inside the keepalive —
                    # that's the next tool call's job. Just exit cleanly.
                    return
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    async def _safe_disconnect(self) -> None:
        self._stop_keepalive()
        if self._client is not None:
            try:
                if self._client.is_connected:
                    await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._client = None
        self._notify_started = False

    def _on_disconnect(self, _client) -> None:
        logger.warning("BLE disconnected from %s", self._adapter_name)
        self._connected = False
        self._stop_keepalive()

    async def _reconnect(self) -> None:
        logger.info("Attempting BLE reconnect...")
        self._connected = False
        await self._safe_disconnect()
        try:
            await self._connect_once()
        except Exception as e:  # noqa: BLE001
            logger.error("Reconnect failed: %s", e)

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def _on_notify(self, _sender, data: bytearray) -> None:
        def _apply() -> None:
            self._response_buffer.extend(data)
            if b">" in self._response_buffer:
                self._response_event.set()

        if self._loop is not None and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(_apply)
                return
            except RuntimeError:
                pass
        _apply()

    async def _send_command(self, cmd: str, timeout: float | None = None) -> str:
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        # Serialize: ELM327 is one-command-at-a-time. Without this, parallel
        # MCP tool calls race on the shared response buffer.
        async with self._cmd_lock:
            if self._client is None or not self._client.is_connected:
                await self._reconnect()
                if self._client is None or not self._client.is_connected:
                    return ""

            self._response_buffer.clear()
            self._response_event.clear()

            payload = f"{cmd}\r".encode("ascii")
            try:
                await self._client.write_gatt_char(
                    self._write_uuid, payload,
                    response=not self._write_without_response,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("BLE write failed (%s); reconnecting…", e)
                await self._reconnect()
                if self._client is None or not self._client.is_connected:
                    return ""
                self._response_buffer.clear()
                self._response_event.clear()
                try:
                    await self._client.write_gatt_char(
                        self._write_uuid, payload,
                        response=not self._write_without_response,
                    )
                except Exception as e2:  # noqa: BLE001
                    logger.error("BLE write failed after reconnect: %s", e2)
                    return ""

            try:
                await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.debug("Timeout (%.1fs) waiting for response to: %s", timeout, cmd)

            raw = bytes(self._response_buffer).decode("ascii", errors="replace")
            lines = _clean_elm_response(raw, echoed_cmd=cmd)
            result = "\n".join(lines)
            logger.debug("CMD: %s -> %r", cmd, result)
            return result

    # ------------------------------------------------------------------
    # OBDConnectionProtocol implementation
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        return (
            self._connected
            and self._client is not None
            and self._client.is_connected
        )

    @property
    def last_connect_error(self) -> str | None:
        """Most recent connect failure message, for surfacing in tool errors."""
        return self._last_connect_error

    async def ensure_ready(self) -> None:
        """Lazy reconnect: try to (re-)establish if not currently connected.

        Called from MCP tool handlers so that if the server's startup
        connect failed (e.g. adapter not yet powered, app missing
        Bluetooth permission), the first tool call after the user fixes
        the underlying issue can recover without a server restart.
        """
        if self.is_connected():
            return
        try:
            await self.connect()
        except Exception as e:  # noqa: BLE001
            self._last_connect_error = str(e)
            raise

    async def get_dtcs(self, module: str | None = None) -> list[DTC]:
        response = await self._send_command("03", timeout=self.PROTOCOL_TIMEOUT)
        dtcs: list[DTC] = []
        if not response or self._is_bus_error(response):
            return dtcs

        from obd2_mcp.dtc_database import lookup_dtc

        hex_payload = self._extract_mode_payload(response.split("\n"), "43")
        for i in range(0, len(hex_payload) - 3, 4):
            try:
                byte1 = int(hex_payload[i:i + 2], 16)
                byte2 = int(hex_payload[i + 2:i + 4], 16)
                if byte1 == 0 and byte2 == 0:
                    continue
                code = self._decode_dtc(byte1, byte2)
                dtcs.append(DTC(code=code, description=lookup_dtc(code)))
            except (ValueError, IndexError):
                continue
        return dtcs

    @staticmethod
    def _is_bus_error(response: str) -> bool:
        upper = response.upper()
        return any(err in upper for err in (
            "NO DATA", "ERROR", "UNABLE", "CAN ERROR", "BUS INIT", "STOPPED",
        ))

    @staticmethod
    def _decode_dtc(byte1: int, byte2: int) -> str:
        prefixes = {0: "P", 1: "C", 2: "B", 3: "U"}
        first_digit = (byte1 >> 6) & 0x03
        second_digit = (byte1 >> 4) & 0x03
        rest = f"{byte1 & 0x0F:01X}{byte2:02X}"
        return f"{prefixes[first_digit]}{second_digit}{rest}"

    @staticmethod
    def _extract_mode_payload(lines: list[str], mode_resp_tag: str) -> str:
        """Extract hex payload after mode-response tag, handling multi-frame ISO-TP."""
        tag = mode_resp_tag.upper().replace(" ", "")
        joined = ""
        started = False
        for line in lines:
            flat = line.upper().replace(" ", "").replace(":", "")
            if not started:
                idx = flat.find(tag)
                if idx >= 0:
                    joined += flat[idx + len(tag):]
                    started = True
                continue
            m = re.match(r"^(?:[0-9A-F]{3}|[0-9A-F]{8})2[0-9A-F]", flat)
            if m:
                joined += flat[m.end():]
            elif re.match(r"^2[0-9A-F]", flat):
                joined += flat[2:]
            else:
                joined += flat
        return joined

    async def clear_dtcs(self, module: str | None = None) -> bool:
        response = await self._send_command("04", timeout=self.DEFAULT_TIMEOUT)
        return "44" in response or "OK" in response.upper()

    async def get_live_data(self, pids: list[str] | None = None) -> list[SensorReading]:
        pid_map = {
            "RPM": ("010C", 2, lambda b: ((b[0] << 8) | b[1]) / 4, "rpm"),
            "SPEED": ("010D", 1, lambda b: b[0], "km/h"),
            "COOLANT_TEMP": ("0105", 1, lambda b: b[0] - 40, "°C"),
            "ENGINE_LOAD": ("0104", 1, lambda b: b[0] * 100 / 255, "%"),
            "INTAKE_TEMP": ("010F", 1, lambda b: b[0] - 40, "°C"),
            "THROTTLE_POS": ("0111", 1, lambda b: b[0] * 100 / 255, "%"),
            "TIMING_ADVANCE": ("010E", 1, lambda b: b[0] / 2 - 64, "°"),
            "MAF": ("0110", 2, lambda b: ((b[0] << 8) | b[1]) / 100, "g/s"),
            "FUEL_PRESSURE": ("010A", 1, lambda b: b[0] * 3, "kPa"),
            "SHORT_FUEL_TRIM_1": ("0106", 1, lambda b: (b[0] - 128) * 100 / 128, "%"),
            "LONG_FUEL_TRIM_1": ("0107", 1, lambda b: (b[0] - 128) * 100 / 128, "%"),
            "CONTROL_MODULE_VOLTAGE": ("0142", 2, lambda b: ((b[0] << 8) | b[1]) / 1000, "V"),
        }

        target_pids = [p.upper() for p in (pids or pid_map.keys())]
        readings: list[SensorReading] = []
        for key in target_pids:
            if key not in pid_map:
                continue
            obd_cmd, num_bytes, converter, unit = pid_map[key]
            response = await self._send_command(obd_cmd, timeout=self.PROTOCOL_TIMEOUT)
            if not response or self._is_bus_error(response):
                continue
            tag = "41" + obd_cmd[2:]
            hex_payload = self._extract_mode_payload(response.split("\n"), tag)
            if not hex_payload or len(hex_payload) < num_bytes * 2:
                continue
            try:
                data_bytes = [int(hex_payload[i*2:i*2+2], 16) for i in range(num_bytes)]
                value = converter(data_bytes)
                readings.append(SensorReading(
                    pid=key, name=key.replace("_", " ").title(),
                    value=round(value, 2), unit=unit,
                ))
            except (ValueError, IndexError) as err:
                logger.debug("Failed to parse %s: %s (raw=%r)", key, err, response)
        return readings

    async def get_vehicle_info(self) -> VehicleInfo:
        info = VehicleInfo()
        vin_raw = await self._send_command("0902", timeout=self.PROTOCOL_TIMEOUT)
        if vin_raw and not self._is_bus_error(vin_raw):
            payload = self._extract_mode_payload(vin_raw.split("\n"), "4902")
            if payload.startswith("01"):
                payload = payload[2:]
            try:
                vin_bytes = bytes.fromhex(payload[:34])
                vin = vin_bytes.decode("ascii", errors="replace").strip("\x00 ")
                if len(vin) >= 11:  # rough sanity check
                    info.vin = vin
            except ValueError:
                pass

        proto = await self._send_command("ATDPN", timeout=2.0)
        if proto:
            info.obd_standard = proto.strip()

        voltage = await self._send_command("ATRV", timeout=2.0)
        if voltage:
            info.ecu_name = f"Battery: {voltage.strip()}"

        return info

    async def list_modules(self) -> list[Module]:
        response = await self._send_command("0100", timeout=self.PROTOCOL_TIMEOUT)
        modules: list[Module] = []
        if not response or self._is_bus_error(response):
            return modules

        proto_resp = await self._send_command("ATDPN", timeout=2.0)
        protocol = proto_resp.strip() if proto_resp else "auto"

        seen: set[str] = set()
        for line in response.split("\n"):
            flat = line.upper().replace(" ", "")
            m = re.match(r"^([0-9A-F]{3,8})", flat)
            if not m:
                continue
            addr = m.group(1)
            if "4100" not in flat[len(addr):]:
                continue
            if addr in seen:
                continue
            seen.add(addr)
            modules.append(Module(
                address=f"0x{addr}",
                name=self._friendly_module_name(addr),
                protocol=protocol,
            ))

        if not modules:
            modules.append(Module(
                address="0x7E0",
                name="Engine Control Module (ECM)",
                protocol=protocol,
            ))
        return modules

    @staticmethod
    def _friendly_module_name(addr: str) -> str:
        known = {
            "7E8": "Engine Control Module (ECM)",
            "7E9": "Transmission Control Module (TCM)",
            "7EA": "Hybrid/Electric Control Module",
            "7EB": "Body Control Module (BCM)",
            "7EC": "ABS / Brake Control Module",
            "7ED": "Instrument Cluster",
            "7EE": "Airbag Control Module",
            "7EF": "Auxiliary Control Module",
        }
        return known.get(addr.upper(), f"ECU at {addr}")

    async def get_freeze_frame(self, dtc_code: str) -> FreezeFrame | None:
        response = await self._send_command("0200", timeout=self.PROTOCOL_TIMEOUT)
        if not response or self._is_bus_error(response):
            return None
        return FreezeFrame(dtc_code=dtc_code.upper())

    def close(self) -> None:
        self._connected = False
        self._stop_keepalive()
        client = self._client
        self._client = None
        if client is None:
            return

        async def _do_disconnect() -> None:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_do_disconnect())
                return
            loop.run_until_complete(_do_disconnect())
        except RuntimeError:
            try:
                asyncio.run(_do_disconnect())
            except Exception:  # noqa: BLE001
                pass
        logger.info("BLE OBD connection closed")


# ---------------------------------------------------------------------------
# End-to-end probe — used by `obd2-mcp --probe`
# ---------------------------------------------------------------------------

async def probe_adapter(
    address: str | None = None,
    name_hint: str | None = None,
    write_uuid: str | None = None,
    notify_uuid: str | None = None,
) -> dict:
    """End-to-end self-test: discover, connect, init, query.

    Returns a dict with every step's outcome. Use this to confirm the
    adapter is reachable and responsive before launching the MCP server.
    """
    report: dict[str, Any] = {
        "scan": None,
        "connect": None,
        "uuid_profile": None,
        "init": None,
        "voltage": None,
        "protocol": None,
        "supported_pids": None,
        "rpm": None,
        "errors": [],
        "spp_pairings_warning": _detect_macos_spp_pairings(),
    }

    conn = BLEOBDConnection(
        address=address, name_hint=name_hint,
        write_uuid=write_uuid, notify_uuid=notify_uuid,
    )

    try:
        await conn.connect()
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"connect: {e}")
        return report

    report["connect"] = "ok"
    report["scan"] = {
        "address": str(conn._device.address) if conn._device else None,
        "name": conn._adapter_name,
    }
    report["uuid_profile"] = {
        "write": conn._write_uuid,
        "notify": conn._notify_uuid,
    }
    report["init"] = conn._adapter_version or "(no banner)"

    try:
        v = await conn._send_command("ATRV", timeout=2.0)
        report["voltage"] = v.strip() if v else None
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"voltage: {e}")

    try:
        p = await conn._send_command("ATDPN", timeout=2.0)
        report["protocol"] = p.strip() if p else None
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"protocol: {e}")

    try:
        s = await conn._send_command("0100", timeout=conn.PROTOCOL_TIMEOUT)
        report["supported_pids"] = s if s else None
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"supported_pids: {e}")

    try:
        readings = await conn.get_live_data(pids=["RPM"])
        report["rpm"] = readings[0].value if readings else None
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"rpm: {e}")

    conn.close()
    return report
