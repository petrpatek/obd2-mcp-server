# obd2-mcp-server

An MCP server that bridges your car's OBD-II diagnostic port to Claude via the [Model Context Protocol](https://modelcontextprotocol.io).

```
┌──────────┐    Bluetooth/USB    ┌───────────────┐    python-obd    ┌──────────────┐    MCP (stdio)    ┌─────────┐
│   Car    │ ◄─────────────────► │  ELM327       │ ◄──────────────► │  MCP Server  │ ◄───────────────► │  Claude │
│  OBD-II  │                     │  Adapter      │                  │  (Python)    │                   │   AI    │
└──────────┘                     └───────────────┘                  └──────────────┘                   └─────────┘
```

## What it does

Claude can read your car's fault codes, live sensor data, and vehicle info — then explain what it all means in plain language. Built for the ["I Let AI Diagnose My Car"](https://bitvea.com) PR stunt using a 2010 Ford Focus CC convertible with a broken passenger window and non-functional roof.

## Status: Alpha (for inspiration, not production)

This is an early alpha built in a weekend. I'm a JavaScript/TypeScript developer who vibe-coded this in Python with Claude's help. It works, it's tested, but it's not production-grade. Use it as inspiration, fork it, improve it — PRs welcome.

## Tools

| Tool | Description |
|------|-------------|
| `read_dtc` | Read Diagnostic Trouble Codes from all or specific modules |
| `clear_dtc` | Clear stored fault codes (with safety confirmation) |
| `get_live_data` | Real-time sensor readings (RPM, temp, speed, etc.) |
| `get_vehicle_info` | VIN, calibration IDs, ECU name |
| `list_modules` | Enumerate all ECUs on the CAN bus |
| `get_freeze_frame` | Sensor snapshot from when a DTC was set |
| `explain_dtc` | Plain-language DTC explanation with vehicle-specific context |

## Quick start

### One-command setup (macOS & Linux)

```bash
./setup.sh
```

The script will find (or install via Homebrew/apt) Python 3.10+, create a virtual environment, and install everything. Then:

```bash
source .venv/bin/activate
obd2-mcp --mock
```

### Manual setup

```bash
# Requires Python 3.10+ (the MCP SDK needs it)
# macOS with Homebrew: brew install python@3.12
# Ubuntu/Debian:       sudo apt install python3.12 python3.12-venv

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
obd2-mcp --mock
```

### With a real car

```bash
source .venv/bin/activate

# Auto-detect adapter
obd2-mcp

# Or specify a port (see "Bluetooth pairing" below for finding the port)
obd2-mcp --port /dev/tty.vLinkerFD-Android
```

### Bluetooth pairing (vLinker FD)

The Vgate vLinker FD connects via Bluetooth. Here's how to pair it:

1. Plug the vLinker FD into your car's OBD-II port (under the steering wheel)
2. Turn the ignition to ON (engine can be off)
3. On your Mac: **System Settings → Bluetooth** — look for "vLinkerFD-Android"
4. Click **Connect** — enter PIN `1234` (not `0000` — the default `0000` does not work)
5. Once paired, find the serial port:
   ```bash
   ls /dev/tty.*vLinker*
   # Expected output: /dev/tty.vLinkerFD-Android
   ```
6. Use that port name with `--port`

### Claude Desktop / Cowork configuration

The config file lives at:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Open it (create if it doesn't exist) and add:

```json
{
  "mcpServers": {
    "obd2": {
      "command": "/full/path/to/obd2-mcp-server/.venv/bin/obd2-mcp",
      "args": ["--mock"]
    }
  }
}
```

**Demo mode** — use `["--mock"]` as shown above. No car needed.

**Real car** — replace the args with your Bluetooth port:
```json
"args": ["--port", "/dev/tty.vLinkerFD-Android"]
```

After saving, **Cmd+Q** Claude Desktop and reopen it. The 7 OBD tools will appear automatically (look for the hammer icon).

Use the full path to the `obd2-mcp` binary inside the venv so Claude Desktop can find it without activating the environment. The setup script prints the exact path at the end.

## Requirements

- Python 3.10+ (the `mcp` SDK requires it — macOS ships with 3.9 which won't work; use `brew install python@3.12`)
- Any ELM327-compatible OBD-II adapter (for real mode)
- Tested with: Vgate vLinker FD (Bluetooth) on macOS

## DTC Code Database

The server includes **1,937 Ford-specific DTC codes** covering all four categories:

- **321** Powertrain (P) + Network (U) codes
- **797** Body (B) codes — doors, windows, mirrors, seats, roof, HVAC
- **819** Chassis (C) codes — ABS, traction control, suspension, steering

Plus a curated set of common SAE-standard codes and Ford Focus CC-specific diagnostic context for convertible roof troubleshooting.

## Development

```bash
./setup.sh                    # or manual venv setup with Python 3.10+
source .venv/bin/activate
pip install -e ".[dev]"

pytest                        # 31 tests
ruff check src/ tests/        # lint
```

## The story behind this

A 2010 Ford Focus CC convertible — passenger window regulator broke, the roof stopped working. Claude predicted the diagnosis before the scan confirmed it: the roof controller does a real-time window position check (not a stored fault), so the Folding Top Control Module shows zero DTCs even though the roof won't move. The real codes (B1310, B166A) were on the Passenger Door Control Unit.

The mock adapter simulates this exact scenario.

## License

MIT
