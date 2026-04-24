"""Tests for the OBD-II MCP Server using the mock adapter."""

from __future__ import annotations

import json
import pytest

from obd2_mcp.mock_obd import MockOBDConnection
from obd2_mcp.dtc_database import lookup_dtc, get_dtc_category, get_dtc_context
from obd2_mcp.server import (
    _handle_read_dtc,
    _handle_clear_dtc,
    _handle_get_live_data,
    _handle_get_vehicle_info,
    _handle_list_modules,
    _handle_get_freeze_frame,
    _handle_explain_dtc,
)
import obd2_mcp.server as server_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_connection():
    """Set up mock OBD connection for all tests."""
    conn = MockOBDConnection()
    server_module.connection = conn
    yield conn
    server_module.connection = None


def _parse(result) -> dict:
    """Parse a TextContent response into a dict."""
    return json.loads(result[0].text)


# ---------------------------------------------------------------------------
# DTC Database tests
# ---------------------------------------------------------------------------

class TestDTCDatabase:
    def test_lookup_known_code(self):
        assert "Power Door Unlock" in lookup_dtc("B1310")

    def test_lookup_unknown_code(self):
        result = lookup_dtc("P9999")
        assert "Unknown" in result

    def test_category_powertrain(self):
        assert "Powertrain" in get_dtc_category("P0420")

    def test_category_body(self):
        assert "Body" in get_dtc_category("B1310")

    def test_category_network(self):
        assert "Network" in get_dtc_category("U0100")

    def test_focus_cc_context_exists(self):
        ctx = get_dtc_context("B1310")
        assert ctx is not None
        assert "convertible" in ctx.lower() or "Focus CC" in ctx

    def test_focus_cc_context_missing(self):
        assert get_dtc_context("P0420") is None


# ---------------------------------------------------------------------------
# Mock adapter tests
# ---------------------------------------------------------------------------

class TestMockOBD:
    def test_is_connected(self, mock_connection):
        assert mock_connection.is_connected()

    def test_get_dtcs_all(self, mock_connection):
        dtcs = mock_connection.get_dtcs()
        codes = [d.code for d in dtcs]
        assert "B1310" in codes
        assert "B166A" in codes

    def test_get_dtcs_specific_module(self, mock_connection):
        dtcs = mock_connection.get_dtcs(module="folding_top")
        assert len(dtcs) == 0  # The key insight — no DTCs on the roof module

    def test_get_dtcs_passenger_door(self, mock_connection):
        dtcs = mock_connection.get_dtcs(module="passenger_door")
        assert len(dtcs) == 2

    def test_clear_dtcs(self, mock_connection):
        mock_connection.clear_dtcs()
        assert mock_connection.get_dtcs() == []

    def test_vehicle_info(self, mock_connection):
        info = mock_connection.get_vehicle_info()
        assert info.vin.startswith("WF0")
        assert "Focus" in info.ecu_name

    def test_list_modules(self, mock_connection):
        modules = mock_connection.list_modules()
        names = [m.name for m in modules]
        assert any("Folding Top" in n for n in names)
        assert any("Passenger Door" in n for n in names)
        assert any("PCM" in n for n in names)

    def test_live_data_all(self, mock_connection):
        readings = mock_connection.get_live_data()
        assert len(readings) > 5
        pids = [r.pid for r in readings]
        assert "RPM" in pids
        assert "COOLANT_TEMP" in pids

    def test_live_data_specific(self, mock_connection):
        readings = mock_connection.get_live_data(pids=["RPM", "SPEED"])
        assert len(readings) == 2

    def test_freeze_frame(self, mock_connection):
        frame = mock_connection.get_freeze_frame("B1310")
        assert frame is not None
        assert frame.dtc_code == "B1310"

    def test_close(self, mock_connection):
        mock_connection.close()
        assert not mock_connection.is_connected()


# ---------------------------------------------------------------------------
# MCP tool handler tests
# ---------------------------------------------------------------------------

class TestMCPTools:
    def test_read_dtc_all(self):
        result = _parse(_handle_read_dtc({}))
        assert result["dtc_count"] == 2
        codes = [d["code"] for d in result["dtcs"]]
        assert "B1310" in codes

    def test_read_dtc_empty_module(self):
        result = _parse(_handle_read_dtc({"module": "folding_top"}))
        assert result["dtc_count"] == 0
        assert "clean" in result["summary"].lower()

    def test_read_dtc_has_context(self):
        result = _parse(_handle_read_dtc({}))
        b1310 = next(d for d in result["dtcs"] if d["code"] == "B1310")
        assert "vehicle_context" in b1310

    def test_clear_dtc_requires_confirm(self):
        result = _parse(_handle_clear_dtc({"module": "passenger_door", "confirm": False}))
        assert "error" in result

    def test_clear_dtc_success(self):
        result = _parse(_handle_clear_dtc({"module": "passenger_door", "confirm": True}))
        assert result["cleared"] is True

    def test_get_live_data(self):
        result = _parse(_handle_get_live_data({}))
        assert result["reading_count"] > 0
        rpm = next(r for r in result["readings"] if r["pid"] == "RPM")
        assert 700 < rpm["value"] < 900

    def test_get_live_data_specific_pids(self):
        result = _parse(_handle_get_live_data({"pids": ["RPM", "COOLANT_TEMP"]}))
        assert result["reading_count"] == 2

    def test_get_vehicle_info(self):
        result = _parse(_handle_get_vehicle_info({}))
        assert result["vin"].startswith("WF0")
        assert "Focus" in result["ecu_name"]

    def test_list_modules(self):
        result = _parse(_handle_list_modules({}))
        assert result["module_count"] == 7
        names = [m["name"] for m in result["modules"]]
        assert any("Folding Top" in n for n in names)

    def test_get_freeze_frame(self):
        result = _parse(_handle_get_freeze_frame({"dtc_code": "B1310"}))
        assert result["dtc_code"] == "B1310"
        assert result["freeze_frame"] is not None

    def test_explain_dtc_known(self):
        result = _parse(_handle_explain_dtc({"dtc_code": "B1310"}))
        assert result["code"] == "B1310"
        assert "Power Door Unlock" in result["description"]
        assert "vehicle_specific_context" in result

    def test_explain_dtc_unknown(self):
        result = _parse(_handle_explain_dtc({"dtc_code": "P9999"}))
        assert result["code"] == "P9999"
        assert "Unknown" in result["description"]

    def test_explain_dtc_has_advice(self):
        result = _parse(_handle_explain_dtc({"dtc_code": "B1310"}))
        assert "general_advice" in result
        assert "Body" in result["general_advice"]
