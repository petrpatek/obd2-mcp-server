"""
Wrapper around python-obd that manages the ELM327 connection
and provides a clean async-friendly interface for the MCP tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DTC:
    """A single Diagnostic Trouble Code."""
    code: str
    description: str
    status: str = "stored"  # stored | pending | permanent


@dataclass
class SensorReading:
    """A single live-data reading."""
    pid: str
    name: str
    value: Any
    unit: str


@dataclass
class FreezeFrame:
    """Snapshot of sensor data captured when a DTC was set."""
    dtc_code: str
    readings: list[SensorReading] = field(default_factory=list)


@dataclass
class VehicleInfo:
    """Basic vehicle identification."""
    vin: str = ""
    calibration_ids: list[str] = field(default_factory=list)
    ecu_name: str = ""
    obd_standard: str = ""


@dataclass
class Module:
    """An ECU / control module on the vehicle bus."""
    address: str
    name: str
    protocol: str = ""


# ---------------------------------------------------------------------------
# Connection protocol (interface)
# ---------------------------------------------------------------------------

class OBDConnectionProtocol(Protocol):
    """Interface that both real and mock connections implement."""

    def is_connected(self) -> bool: ...
    def get_dtcs(self, module: str | None = None) -> list[DTC]: ...
    def clear_dtcs(self, module: str | None = None) -> bool: ...
    def get_live_data(self, pids: list[str] | None = None) -> list[SensorReading]: ...
    def get_vehicle_info(self) -> VehicleInfo: ...
    def list_modules(self) -> list[Module]: ...
    def get_freeze_frame(self, dtc_code: str) -> FreezeFrame | None: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Real OBD connection (python-obd)
# ---------------------------------------------------------------------------

class RealOBDConnection:
    """Connects to a real ELM327-compatible adapter via python-obd."""

    # Bluetooth adapters (like vLinker FD) typically use lower baud rates.
    # USB adapters use 115200 or 500000. We try common rates in order.
    BAUD_RATES = [500000, 115200, 38400, 9600]

    def __init__(self, port: str | None = None, baudrate: int | None = None, fast: bool = True):
        try:
            import obd as obd_lib
        except ImportError:
            raise ImportError(
                "python-obd is required for real OBD connections. "
                "Install it with: pip install obd"
            )
        self._obd = obd_lib
        self._conn = None

        if baudrate:
            # Explicit baud rate — try just that one
            logger.info("Connecting to OBD adapter (port=%s, baudrate=%d) ...", port, baudrate)
            self._conn = obd_lib.OBD(portstr=port, baudrate=baudrate, fast=fast)
        else:
            # Auto-detect: try common baud rates
            for rate in self.BAUD_RATES:
                logger.info("Trying baud rate %d on %s ...", rate, port or "auto")
                try:
                    self._conn = obd_lib.OBD(portstr=port, baudrate=rate, fast=fast)
                    if self._conn.is_connected():
                        break
                    self._conn.close()
                    self._conn = None
                except Exception as e:
                    logger.debug("Baud rate %d failed: %s", rate, e)
                    self._conn = None

            if self._conn is None:
                # Last resort — let python-obd fully auto-detect
                logger.info("Auto-detecting adapter (no baud rate constraint) ...")
                self._conn = obd_lib.OBD(portstr=port, fast=fast)

        if self._conn.is_connected():
            logger.info("Connected: %s (protocol: %s)", self._conn.port_name(), self._conn.protocol_name())
        else:
            logger.warning("OBD adapter not found — running in disconnected state. "
                           "Check: is ignition ON? Is the adapter paired and the port correct?")

    def is_connected(self) -> bool:
        return self._conn.is_connected()

    def get_dtcs(self, module: str | None = None) -> list[DTC]:
        cmd = self._obd.commands.GET_DTC
        response = self._conn.query(cmd)
        if response.is_null():
            return []
        dtcs: list[DTC] = []
        for code, desc in response.value:
            dtcs.append(DTC(code=code, description=desc or "Unknown"))
        return dtcs

    def clear_dtcs(self, module: str | None = None) -> bool:
        cmd = self._obd.commands.CLEAR_DTC
        response = self._conn.query(cmd)
        return not response.is_null()

    def get_live_data(self, pids: list[str] | None = None) -> list[SensorReading]:
        readings: list[SensorReading] = []
        if pids:
            for pid_name in pids:
                if hasattr(self._obd.commands, pid_name.upper()):
                    cmd = getattr(self._obd.commands, pid_name.upper())
                    response = self._conn.query(cmd)
                    if not response.is_null():
                        val = response.value
                        unit = str(val.units) if hasattr(val, "units") else ""
                        magnitude = val.magnitude if hasattr(val, "magnitude") else val
                        readings.append(SensorReading(
                            pid=pid_name,
                            name=cmd.name,
                            value=magnitude,
                            unit=unit,
                        ))
        else:
            # Return a standard set of common PIDs
            for cmd in self._obd.commands[1]:  # mode 01
                if cmd and self._conn.supports(cmd):
                    response = self._conn.query(cmd)
                    if not response.is_null():
                        val = response.value
                        unit = str(val.units) if hasattr(val, "units") else ""
                        magnitude = val.magnitude if hasattr(val, "magnitude") else val
                        readings.append(SensorReading(
                            pid=cmd.name,
                            name=cmd.desc,
                            value=magnitude,
                            unit=unit,
                        ))
        return readings

    def get_vehicle_info(self) -> VehicleInfo:
        info = VehicleInfo()
        # VIN
        if hasattr(self._obd.commands, "VIN"):
            resp = self._conn.query(self._obd.commands.VIN)
            if not resp.is_null():
                info.vin = str(resp.value)
        # ECU name
        if hasattr(self._obd.commands, "ECU_NAME"):
            resp = self._conn.query(self._obd.commands.ECU_NAME)
            if not resp.is_null():
                info.ecu_name = str(resp.value)
        return info

    def list_modules(self) -> list[Module]:
        # python-obd doesn't enumerate modules directly;
        # we report what protocols we see
        modules: list[Module] = []
        if self._conn.is_connected():
            proto = self._conn.protocol_name()
            modules.append(Module(
                address="0x7E0",
                name="Engine Control Module (ECM)",
                protocol=proto,
            ))
        return modules

    def get_freeze_frame(self, dtc_code: str) -> FreezeFrame | None:
        cmd = self._obd.commands.FREEZE_DTC
        response = self._conn.query(cmd)
        if response.is_null():
            return None
        frame = FreezeFrame(dtc_code=dtc_code)
        return frame

    def close(self) -> None:
        self._conn.close()
        logger.info("OBD connection closed")
