"""
Mock OBD adapter that simulates a vehicle with realistic fault codes.

Use this for demos, conference talks, and testing without a physical car.
The default mock simulates a 2010 Ford Focus CC convertible, but the
architecture is brand-agnostic — any vehicle can be simulated.
"""

from __future__ import annotations

import random
from obd2_mcp.obd_connection import (
    DTC,
    FreezeFrame,
    Module,
    SensorReading,
    VehicleInfo,
)
from obd2_mcp.dtc_database import lookup_dtc


class MockOBDConnection:
    """
    Simulates a vehicle with common DTCs and realistic live sensor data.

    Default: a warm-idling diesel with two body DTCs on the passenger door
    module and clean readings everywhere else.
    """

    def __init__(self) -> None:
        self._connected = True
        self._dtcs_cleared = False

    def is_connected(self) -> bool:
        return self._connected

    def get_dtcs(self, module: str | None = None) -> list[DTC]:
        if self._dtcs_cleared:
            return []

        all_dtcs: dict[str, list[DTC]] = {
            "passenger_door": [
                DTC(code="B1310", description=lookup_dtc("B1310", brand="ford"), status="stored"),
                DTC(code="B166A", description=lookup_dtc("B166A", brand="ford"), status="stored"),
            ],
            "pcm": [],
            "folding_top": [],
            "bcm": [],
            "abs": [],
            "driver_door": [],
            "instrument_cluster": [],
        }

        if module:
            key = module.lower().replace(" ", "_")
            return all_dtcs.get(key, [])

        # Return all DTCs across all modules
        result: list[DTC] = []
        for dtc_list in all_dtcs.values():
            result.extend(dtc_list)
        return result

    def clear_dtcs(self, module: str | None = None) -> bool:
        self._dtcs_cleared = True
        return True

    def get_live_data(self, pids: list[str] | None = None) -> list[SensorReading]:
        """Simulate a warm-idling diesel engine."""
        all_data: dict[str, SensorReading] = {
            "RPM": SensorReading(
                pid="RPM", name="Engine RPM",
                value=round(780 + random.uniform(-20, 20), 1), unit="rpm",
            ),
            "SPEED": SensorReading(
                pid="SPEED", name="Vehicle Speed",
                value=0, unit="km/h",
            ),
            "COOLANT_TEMP": SensorReading(
                pid="COOLANT_TEMP", name="Engine Coolant Temperature",
                value=round(87 + random.uniform(-2, 2), 1), unit="°C",
            ),
            "ENGINE_LOAD": SensorReading(
                pid="ENGINE_LOAD", name="Calculated Engine Load",
                value=round(18.5 + random.uniform(-3, 3), 1), unit="%",
            ),
            "INTAKE_TEMP": SensorReading(
                pid="INTAKE_TEMP", name="Intake Air Temperature",
                value=round(24 + random.uniform(-1, 1), 1), unit="°C",
            ),
            "MAF": SensorReading(
                pid="MAF", name="Mass Air Flow Rate",
                value=round(3.2 + random.uniform(-0.5, 0.5), 2), unit="g/s",
            ),
            "THROTTLE_POS": SensorReading(
                pid="THROTTLE_POS", name="Throttle Position",
                value=round(14.5 + random.uniform(-1, 1), 1), unit="%",
            ),
            "FUEL_PRESSURE": SensorReading(
                pid="FUEL_PRESSURE", name="Fuel Rail Pressure",
                value=round(350 + random.uniform(-10, 10), 0), unit="kPa",
            ),
            "TIMING_ADVANCE": SensorReading(
                pid="TIMING_ADVANCE", name="Timing Advance",
                value=round(12.5 + random.uniform(-1, 1), 1), unit="°",
            ),
            "SHORT_FUEL_TRIM_1": SensorReading(
                pid="SHORT_FUEL_TRIM_1", name="Short Term Fuel Trim — Bank 1",
                value=round(random.uniform(-3, 3), 1), unit="%",
            ),
            "LONG_FUEL_TRIM_1": SensorReading(
                pid="LONG_FUEL_TRIM_1", name="Long Term Fuel Trim — Bank 1",
                value=round(1.5 + random.uniform(-1, 1), 1), unit="%",
            ),
            "CONTROL_MODULE_VOLTAGE": SensorReading(
                pid="CONTROL_MODULE_VOLTAGE", name="Control Module Voltage",
                value=round(13.8 + random.uniform(-0.3, 0.3), 1), unit="V",
            ),
            "AMBIANT_AIR_TEMP": SensorReading(
                pid="AMBIANT_AIR_TEMP", name="Ambient Air Temperature",
                value=round(22 + random.uniform(-1, 1), 1), unit="°C",
            ),
            "RUN_TIME": SensorReading(
                pid="RUN_TIME", name="Time Since Engine Start",
                value=random.randint(120, 600), unit="s",
            ),
        }

        if pids:
            return [all_data[p.upper()] for p in pids if p.upper() in all_data]
        return list(all_data.values())

    def get_vehicle_info(self) -> VehicleInfo:
        return VehicleInfo(
            vin="WVWZZZ3CZWE000000",  # Generic demo VIN
            calibration_ids=["DEMO-CAL-001", "DEMO-CAL-002"],
            ecu_name="Demo Vehicle — PCM",
            obd_standard="ISO 15765-4 (CAN 11/500)",
        )

    def list_modules(self) -> list[Module]:
        return [
            Module(address="0x7E0", name="Powertrain Control Module (PCM)", protocol="HS-CAN"),
            Module(address="0x726", name="Passenger Door Control Unit", protocol="MS-CAN"),
            Module(address="0x725", name="Driver Door Control Unit", protocol="MS-CAN"),
            Module(address="0x740", name="Body Control Module (BCM)", protocol="MS-CAN"),
            Module(address="0x760", name="ABS Module", protocol="HS-CAN"),
            Module(address="0x720", name="Instrument Cluster", protocol="MS-CAN"),
        ]

    def get_freeze_frame(self, dtc_code: str) -> FreezeFrame | None:
        """Body codes typically don't store freeze frame data."""
        if dtc_code.upper() in ("B1310", "B166A"):
            return FreezeFrame(
                dtc_code=dtc_code.upper(),
                readings=[
                    SensorReading(
                        pid="CONTROL_MODULE_VOLTAGE",
                        name="Control Module Voltage",
                        value=13.7,
                        unit="V",
                    ),
                    SensorReading(
                        pid="AMBIANT_AIR_TEMP",
                        name="Ambient Air Temperature",
                        value=19.0,
                        unit="°C",
                    ),
                ],
            )
        return None

    def close(self) -> None:
        self._connected = False
