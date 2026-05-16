"""
OBD-II MCP Server — bridges a car's OBD-II diagnostic port to Claude.

Usage:
    # With a real OBD adapter:
    obd2-mcp

    # Demo mode (mock adapter, no car needed):
    obd2-mcp --mock

    # Specify a serial port:
    obd2-mcp --port /dev/tty.usbserial-1234
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from obd2_mcp.dtc_database import (
    find_codes_for_brand,
    get_dtc_category,
    get_procedures,
    list_brands,
    lookup_dtc_full,
)
from obd2_mcp.mock_obd import MockOBDConnection
from obd2_mcp.obd_connection import OBDConnectionProtocol, RealOBDConnection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = Server("obd2-mcp-server")
connection: OBDConnectionProtocol | None = None


def _json_response(data: Any) -> list[TextContent]:
    """Wrap a dict/list as a JSON TextContent response."""
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def _error_response(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": message}, indent=2))]


def _ensure_connected() -> OBDConnectionProtocol:
    if connection is None:
        raise RuntimeError("OBD adapter not initialized")
    if not connection.is_connected():
        # Surface the most recent BLE connect error if we have one.
        last = getattr(connection, "last_connect_error", None)
        if last:
            raise RuntimeError(
                f"OBD adapter is not connected. Last connect error:\n{last}"
            )
        raise RuntimeError("OBD adapter is not connected")
    return connection


async def _ensure_ready() -> OBDConnectionProtocol:
    """Async wrapper that triggers a lazy reconnect for BLE if not connected."""
    if connection is None:
        raise RuntimeError("OBD adapter not initialized")
    if not connection.is_connected():
        ensure_ready = getattr(connection, "ensure_ready", None)
        if ensure_ready is not None:
            try:
                await ensure_ready()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"OBD adapter could not be (re)connected: {e}") from e
        else:
            raise RuntimeError("OBD adapter is not connected")
    return connection


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="read_dtc",
        description=(
            "Read Diagnostic Trouble Codes (DTCs) from the vehicle. "
            "Scans all modules by default, or a specific module if specified. "
            "Returns fault codes with descriptions and status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": (
                        "Specific module to scan (e.g. 'passenger_door', 'pcm', 'folding_top'). "
                        "Omit to scan all modules."
                    ),
                },
            },
        },
    ),
    Tool(
        name="clear_dtc",
        description=(
            "Clear stored Diagnostic Trouble Codes. "
            "WARNING: This clears fault memory — codes will be lost until they recur. "
            "Requires explicit confirmation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "Module to clear codes from. Required.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to proceed. Safety check.",
                },
            },
            "required": ["module", "confirm"],
        },
    ),
    Tool(
        name="get_live_data",
        description=(
            "Read real-time sensor values from the vehicle (engine RPM, coolant temp, speed, etc.). "
            "Specify PIDs for targeted readings, or omit for all available sensors."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of PIDs to read (e.g. ['RPM', 'COOLANT_TEMP', 'SPEED']). "
                        "Omit for all available readings."
                    ),
                },
            },
        },
    ),
    Tool(
        name="get_vehicle_info",
        description=(
            "Read vehicle identification: VIN, calibration IDs, ECU name, and OBD standard."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="list_modules",
        description=(
            "List all ECUs (Electronic Control Units) found on the vehicle's CAN bus. "
            "Shows module name, address, and communication protocol."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="get_freeze_frame",
        description=(
            "Read freeze-frame data — a snapshot of sensor values captured at the moment "
            "a DTC was set. Useful for understanding the conditions that triggered a fault."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dtc_code": {
                    "type": "string",
                    "description": "The DTC code to retrieve freeze frame for (e.g. 'P0420', 'B1310').",
                },
            },
            "required": ["dtc_code"],
        },
    ),
    Tool(
        name="explain_dtc",
        description=(
            "Get a detailed, plain-language explanation of a Diagnostic Trouble Code. "
            "Includes the code description, category, and manufacturer-specific context "
            "when a brand is specified."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dtc_code": {
                    "type": "string",
                    "description": "The DTC code to explain (e.g. 'P0420', 'B1310', 'U0100').",
                },
                "brand": {
                    "type": "string",
                    "description": (
                        "Vehicle brand slug (e.g. 'ford', 'toyota', 'bmw'). "
                        "Provides brand-specific descriptions when available."
                    ),
                },
            },
            "required": ["dtc_code"],
        },
    ),
    Tool(
        name="list_brands",
        description=(
            "List all vehicle brands in the DTC database. "
            "Returns brand slugs, names, and code counts. "
            "Use this to discover which brands are supported."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="search_dtc_database",
        description=(
            "Search the DTC database for a code with full details. "
            "Returns the complete entry including fault location, probable cause, "
            "detail URL, and which brand/source the code was found in."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dtc_code": {
                    "type": "string",
                    "description": "The DTC code to look up (e.g. 'P0420', 'B1310').",
                },
                "brand": {
                    "type": "string",
                    "description": "Optional brand slug to prioritize (e.g. 'ford', 'toyota').",
                },
            },
            "required": ["dtc_code"],
        },
    ),
    Tool(
        name="get_brand_procedures",
        description=(
            "Get diagnostic procedures for a vehicle brand — how to access codes, "
            "clear codes, reset service intervals, run self-tests, etc."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "brand": {
                    "type": "string",
                    "description": "Brand slug (e.g. 'ford', 'toyota', 'bmw').",
                },
                "procedure_type": {
                    "type": "string",
                    "description": (
                        "Filter by type: 'accessing', 'clearing', 'reset', 'test', 'general'. "
                        "Omit for all procedures."
                    ),
                },
            },
            "required": ["brand"],
        },
    ),
    Tool(
        name="list_brand_codes",
        description=(
            "List all manufacturer-specific DTC codes for a given brand. "
            "Returns code, fault location, probable cause, and detail URL for each."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "brand": {
                    "type": "string",
                    "description": "Brand slug (e.g. 'ford', 'toyota', 'bmw').",
                },
            },
            "required": ["brand"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


async def _call_method(method_name: str, **kwargs):
    """Call a method on the connection, supporting both sync and async implementations."""
    conn = await _ensure_ready()
    method = getattr(conn, method_name)
    result = method(**kwargs)
    # If the connection returns a coroutine (BLE), await it
    if asyncio.iscoroutine(result):
        return await result
    return result


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "read_dtc":
            return await _handle_read_dtc(arguments)
        elif name == "clear_dtc":
            return await _handle_clear_dtc(arguments)
        elif name == "get_live_data":
            return await _handle_get_live_data(arguments)
        elif name == "get_vehicle_info":
            return await _handle_get_vehicle_info(arguments)
        elif name == "list_modules":
            return await _handle_list_modules(arguments)
        elif name == "get_freeze_frame":
            return await _handle_get_freeze_frame(arguments)
        elif name == "explain_dtc":
            return _handle_explain_dtc(arguments)
        elif name == "list_brands":
            return _handle_list_brands(arguments)
        elif name == "search_dtc_database":
            return _handle_search_dtc_database(arguments)
        elif name == "get_brand_procedures":
            return _handle_get_brand_procedures(arguments)
        elif name == "list_brand_codes":
            return _handle_list_brand_codes(arguments)
        else:
            return _error_response(f"Unknown tool: {name}")
    except Exception as e:
        logger.exception("Tool error: %s", name)
        return _error_response(str(e))


async def _handle_read_dtc(args: dict[str, Any]) -> list[TextContent]:
    module = args.get("module")
    dtcs = await _call_method("get_dtcs", module=module)

    if not dtcs:
        return _json_response({
            "module": module or "all",
            "dtc_count": 0,
            "dtcs": [],
            "summary": "No Diagnostic Trouble Codes found." + (
                f" Module '{module}' reports clean." if module else
                " All scanned modules report clean."
            ),
        })

    dtc_list = []
    for dtc in dtcs:
        entry = asdict(dtc)
        entry["category"] = get_dtc_category(dtc.code)
        full = lookup_dtc_full(dtc.code)
        if full.get("faultLocation"):
            entry["description"] = full["faultLocation"]
        if full.get("probableCause"):
            entry["probable_cause"] = full["probableCause"]
        dtc_list.append(entry)

    return _json_response({
        "module": module or "all",
        "dtc_count": len(dtc_list),
        "dtcs": dtc_list,
    })


async def _handle_clear_dtc(args: dict[str, Any]) -> list[TextContent]:
    if not args.get("confirm"):
        return _error_response(
            "Safety check: set 'confirm' to true to clear DTCs. "
            "This will erase stored fault codes from the module's memory."
        )

    module = args["module"]
    success = await _call_method("clear_dtcs", module=module)

    return _json_response({
        "module": module,
        "cleared": success,
        "warning": (
            "DTCs cleared. Codes will reappear if the underlying fault persists. "
            "Drive the vehicle and re-scan to verify the repair."
        ),
    })


async def _handle_get_live_data(args: dict[str, Any]) -> list[TextContent]:
    pids = args.get("pids")
    readings = await _call_method("get_live_data", pids=pids)

    data = []
    for r in readings:
        data.append({
            "pid": r.pid,
            "name": r.name,
            "value": r.value,
            "unit": r.unit,
        })

    return _json_response({
        "reading_count": len(data),
        "readings": data,
    })


async def _handle_get_vehicle_info(args: dict[str, Any]) -> list[TextContent]:
    info = await _call_method("get_vehicle_info")
    return _json_response(asdict(info))


async def _handle_list_modules(args: dict[str, Any]) -> list[TextContent]:
    modules = await _call_method("list_modules")
    return _json_response({
        "module_count": len(modules),
        "modules": [asdict(m) for m in modules],
    })


async def _handle_get_freeze_frame(args: dict[str, Any]) -> list[TextContent]:
    dtc_code = args["dtc_code"]
    frame = await _call_method("get_freeze_frame", dtc_code=dtc_code)

    if frame is None:
        return _json_response({
            "dtc_code": dtc_code,
            "freeze_frame": None,
            "note": (
                f"No freeze-frame data available for {dtc_code}. "
                "Body and network codes often don't capture freeze-frame snapshots."
            ),
        })

    return _json_response({
        "dtc_code": frame.dtc_code,
        "freeze_frame": [asdict(r) for r in frame.readings],
    })


def _handle_explain_dtc(args: dict[str, Any]) -> list[TextContent]:
    code = args["dtc_code"].upper().strip()
    brand = args.get("brand")
    full = lookup_dtc_full(code, brand=brand)
    category = get_dtc_category(code)

    explanation: dict[str, Any] = {
        "code": code,
        "description": full.get("faultLocation") or "Unknown code",
        "category": category,
        "source": full.get("source"),
    }

    if full.get("probableCause"):
        explanation["probable_cause"] = full["probableCause"]
    if full.get("detailUrl"):
        explanation["detail_url"] = full["detailUrl"]

    prefix = code[0] if code else ""
    if prefix == "P":
        explanation["general_advice"] = (
            "Powertrain codes affect engine and transmission. "
            "Check related sensors, wiring, and connectors. "
            "A code reader can clear the code, but the underlying issue should be addressed first."
        )
    elif prefix == "B":
        explanation["general_advice"] = (
            "Body codes relate to comfort, convenience, and safety systems "
            "(doors, windows, mirrors, seats, roof). Check wiring harness connectors, "
            "especially at door jambs where flexing causes wear."
        )
    elif prefix == "C":
        explanation["general_advice"] = (
            "Chassis codes relate to ABS, traction control, and stability systems. "
            "Wheel speed sensors and their wiring are common failure points."
        )
    elif prefix == "U":
        explanation["general_advice"] = (
            "Network codes indicate communication failures between modules on the CAN bus. "
            "Check for damaged wiring, corrosion, or a failed module."
        )

    return _json_response(explanation)


def _handle_list_brands(args: dict[str, Any]) -> list[TextContent]:
    brands = list_brands()
    return _json_response({
        "brand_count": len(brands),
        "brands": brands,
    })


def _handle_search_dtc_database(args: dict[str, Any]) -> list[TextContent]:
    code = args["dtc_code"].upper().strip()
    brand = args.get("brand")
    result = lookup_dtc_full(code, brand=brand)
    return _json_response(result)


def _handle_get_brand_procedures(args: dict[str, Any]) -> list[TextContent]:
    brand = args["brand"]
    procedure_type = args.get("procedure_type")
    procs = get_procedures(brand, procedure_type=procedure_type)
    return _json_response({
        "brand": brand,
        "procedure_count": len(procs),
        "procedures": procs,
    })


def _handle_list_brand_codes(args: dict[str, Any]) -> list[TextContent]:
    brand = args["brand"]
    codes = find_codes_for_brand(brand)
    return _json_response({
        "brand": brand,
        "code_count": len(codes),
        "codes": codes,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="obd2-mcp",
        description="OBD-II MCP Server — connect your car's diagnostics to Claude",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the mock adapter (demo mode, no car needed)",
    )
    parser.add_argument(
        "--port",
        type=str,
        default=None,
        help="Serial port for the OBD adapter (e.g. /dev/tty.usbserial-1234)",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=None,
        help="Baud rate for serial connection (default: auto-detect)",
    )
    parser.add_argument(
        "--ble",
        type=str,
        nargs="?",
        const="auto",
        default=None,
        metavar="ADDRESS",
        help=(
            "Connect via BLE. Pass a device address/UUID, or just --ble to "
            "auto-discover the OBD adapter (scans for vLinker, OBDLink, etc.)"
        ),
    )
    parser.add_argument(
        "--ble-name",
        type=str,
        default=None,
        help="Name hint for BLE auto-discovery (e.g. 'vLinker')",
    )
    parser.add_argument(
        "--ble-write-uuid",
        type=str,
        default=None,
        help="BLE GATT write characteristic UUID (auto-detected if omitted)",
    )
    parser.add_argument(
        "--ble-notify-uuid",
        type=str,
        default=None,
        help="BLE GATT notify characteristic UUID (auto-detected if omitted)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help=(
            "Run an end-to-end BLE diagnostic (scan → connect → init → "
            "query) and print a report. Does not start the MCP server. "
            "Combine with --ble / --ble-name to scope the scan."
        ),
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


async def run_server(args: argparse.Namespace) -> None:
    global connection

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,  # MCP uses stdout for protocol; logs go to stderr
    )

    if args.probe:
        from obd2_mcp.ble_connection import probe_adapter
        address = None
        if args.ble and args.ble != "auto":
            address = args.ble
        report = await probe_adapter(
            address=address,
            name_hint=args.ble_name,
            write_uuid=args.ble_write_uuid,
            notify_uuid=args.ble_notify_uuid,
        )
        # Print to stdout (probe is interactive, not MCP)
        print(json.dumps(report, indent=2, default=str))
        if report.get("errors") and report.get("connect") != "ok":
            sys.exit(1)
        return

    if args.mock:
        logger.info("Starting in MOCK mode (simulated vehicle)")
        connection = MockOBDConnection()
    elif args.ble is not None:
        address = args.ble if args.ble != "auto" else None
        if address:
            logger.info("Connecting via BLE to %s ...", address)
        else:
            logger.info("Auto-discovering BLE OBD adapter...")
        from obd2_mcp.ble_connection import BLEOBDConnection
        ble_conn = BLEOBDConnection(
            address=address,
            write_uuid=args.ble_write_uuid,
            notify_uuid=args.ble_notify_uuid,
            name_hint=args.ble_name,
        )
        try:
            await ble_conn.connect()
        except Exception as e:
            logger.error("BLE connection failed: %s", e)
        connection = ble_conn
    else:
        logger.info("Connecting to real OBD adapter...")
        connection = RealOBDConnection(port=args.port, baudrate=args.baudrate or None)

    logger.info("OBD-II MCP Server ready — %d tools available", len(TOOLS))

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    except asyncio.CancelledError:
        pass
    finally:
        if connection is not None:
            connection.close()
            logger.info("Shutdown complete")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(run_server(args))
    except KeyboardInterrupt:
        pass  # Clean exit on Ctrl+C


if __name__ == "__main__":
    main()
