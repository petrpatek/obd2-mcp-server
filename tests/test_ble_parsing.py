"""
Tests for the BLE OBD connection's parsing / framing logic.

These run without any hardware — they feed hand-crafted ELM327 responses
through the pure helper functions to make sure multi-frame CAN payloads,
fragmented prompts, and SEARCHING markers are handled correctly.
"""

from __future__ import annotations

import pytest

pytest.importorskip("bleak")  # skip whole file if bleak isn't installed

from obd2_mcp.ble_connection import (  # noqa: E402
    BLEOBDConnection,
    KNOWN_UUID_SETS,
    _clean_elm_response,
    _join_hex_payload,
)


class TestCleanElmResponse:
    def test_strips_prompt(self):
        assert _clean_elm_response("ELM327 v1.5\r>") == ["ELM327 v1.5"]

    def test_strips_echo_when_ate0_not_applied(self):
        raw = "ATZ\rELM327 v1.5\r>"
        assert _clean_elm_response(raw, echoed_cmd="ATZ") == ["ELM327 v1.5"]

    def test_strips_searching(self):
        raw = "SEARCHING...\r7E8 06 41 00 BE 3F B8 13 00\r>"
        assert _clean_elm_response(raw) == ["7E8 06 41 00 BE 3F B8 13 00"]

    def test_handles_multiple_blank_lines(self):
        raw = "\r\r\r41 0C 1A F8\r\r>"
        assert _clean_elm_response(raw) == ["41 0C 1A F8"]

    def test_case_insensitive_echo_strip(self):
        assert _clean_elm_response("atz\rELM327\r>", echoed_cmd="ATZ") == ["ELM327"]


class TestJoinHexPayload:
    def test_single_line_with_spaces(self):
        assert _join_hex_payload(["41 00 BE 3F B8 13"]) == "4100BE3FB813"

    def test_ignores_non_hex_lines(self):
        assert _join_hex_payload(["NO DATA", "4100BE"]) == "4100BE"

    def test_strips_frame_index_colons(self):
        lines = ["0: 10 14 49 02 01", "1: 21 50 58 4C 54"]
        result = _join_hex_payload(lines)
        # Colons should be stripped; the hex content of both lines concatenates.
        assert "49" in result
        assert "50584C54" in result


class TestExtractModePayload:
    """Verify ISO-TP multi-frame payload extraction for DTCs and VIN."""

    def test_single_frame_mode_01(self):
        # Mode 01 PID 00 response, headers on (with 7E8 responder + '06' PCI)
        lines = ["7E8 06 41 00 BE 3F B8 13 00"]
        payload = BLEOBDConnection._extract_mode_payload(lines, "4100")
        assert payload.startswith("BE3FB813")

    def test_mode_03_no_dtcs(self):
        # Mode 03 with zero DTCs — response is '43 00' (no pairs to decode)
        lines = ["7E8 02 43 00"]
        payload = BLEOBDConnection._extract_mode_payload(lines, "43")
        # Just '00' or empty remaining — no decodable DTCs
        assert len(payload) <= 4

    def test_mode_03_single_dtc(self):
        # Classic emissions P0420: bytes 0x04 0x20
        lines = ["7E8 04 43 01 04 20 00"]
        payload = BLEOBDConnection._extract_mode_payload(lines, "43")
        # After '43' we expect: 01 04 20 00 (count, DTC bytes, pad)
        assert payload.startswith("01")
        assert "0420" in payload

    def test_mode_09_vin_multi_frame(self):
        """VIN (Mode 09 PID 02) is typically 3 CAN frames. Test the extractor."""
        vin_ascii = b"1FDXLT84DKB23456X"  # 17 chars
        assert len(vin_ascii) == 17
        # ELM327 headers-on multi-frame form (simplified):
        #   7E8 10 14 49 02 01 31 46 44    ← first frame: 4902 01 "1FD"
        #   7E8 21 58 4C 54 38 34 44 4B    ← continuation: "XLT84DK"
        #   7E8 22 42 32 33 34 35 36 58    ← continuation: "B23456X"
        lines = [
            "7E8 10 14 49 02 01 " + " ".join(f"{b:02X}" for b in vin_ascii[0:3]),
            "7E8 21 " + " ".join(f"{b:02X}" for b in vin_ascii[3:10]),
            "7E8 22 " + " ".join(f"{b:02X}" for b in vin_ascii[10:17]),
        ]
        payload = BLEOBDConnection._extract_mode_payload(lines, "4902")
        # Strip the '01' NODI byte
        assert payload.startswith("01")
        payload = payload[2:]
        vin_decoded = bytes.fromhex(payload[:34]).decode("ascii", errors="replace")
        assert vin_decoded == vin_ascii.decode("ascii")


class TestDecodeDtc:
    def test_powertrain(self):
        # P0420 -> bytes (0x04, 0x20)
        assert BLEOBDConnection._decode_dtc(0x04, 0x20) == "P0420"

    def test_body(self):
        # B1310 -> prefix=B (10), first_digit=0b01 -> (0x4 high bits) actually
        # B1310 encodes as: first two bits=10 (Body), then '1310' as hex-ish
        # Per SAE J2012: byte1 bits 7-6 = 10 (Body), bits 5-4 = 01 (second digit 1),
        # bits 3-0 = 3 (first hex of '310'), byte2 = 0x10
        assert BLEOBDConnection._decode_dtc(0b10010011, 0x10) == "B1310"

    def test_chassis(self):
        # C0035 -> 01 00 00 35
        assert BLEOBDConnection._decode_dtc(0b01000000, 0x35) == "C0035"

    def test_network(self):
        # U0100
        assert BLEOBDConnection._decode_dtc(0b11000001, 0x00) == "U0100"


class TestFriendlyModuleName:
    def test_known(self):
        assert "ECM" in BLEOBDConnection._friendly_module_name("7E8")
        assert "TCM" in BLEOBDConnection._friendly_module_name("7E9")

    def test_unknown_falls_through(self):
        assert "7FF" in BLEOBDConnection._friendly_module_name("7FF")


class TestKnownUuidSets:
    def test_fff0_profile_is_first(self):
        """vLinker FD uses FFF0 — make sure we try it first."""
        assert KNOWN_UUID_SETS[0]["service"].startswith("0000fff0")

    def test_all_sets_have_required_keys(self):
        for s in KNOWN_UUID_SETS:
            assert {"name", "service", "write", "notify"} <= s.keys()

    def test_no_18f0_profile(self):
        """18F0 was mistakenly listed — research confirms it's not ELM327."""
        for s in KNOWN_UUID_SETS:
            assert not s["service"].startswith("000018f0")
