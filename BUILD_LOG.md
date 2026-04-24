# Build Log & Lessons Learned

Notes from building obd2-mcp-server — raw material for the blog posts, X thread, and LinkedIn content.

## The Build Timeline

**Date:** April 23, 2026
**Duration:** Single session, from spec to working MCP server connected to Claude Desktop

### What we built
- Python MCP server with 7 tools (read_dtc, clear_dtc, get_live_data, get_vehicle_info, list_modules, get_freeze_frame, explain_dtc)
- Mock adapter simulating a 2010 Ford Focus CC with real fault codes (B1310, B166A)
- 1,937 Ford-specific DTC codes scraped and integrated (321 powertrain, 797 body, 819 chassis)
- Setup script that handles Python version detection, Homebrew install, venv creation
- Full test suite (31 tests, all passing)

### The actual code is small
The MCP server itself is ~250 lines of Python. The tool definitions, the connection wrapper, and the DTC database are the bulk of the project. The "50 lines of Python" claim from the spec is accurate for just the core tool registration.

---

## Gotchas & Lessons Learned

### 1. macOS ships with Python 3.9 (from Xcode) — MCP SDK needs 3.10+
- **What happened:** `pip install -e .` failed because the system Python is too old for the `mcp` package
- **The fix:** Must use Homebrew Python (`brew install python@3.12`) and create a venv with it
- **Content angle:** This is a real friction point for anyone trying to build MCP servers on a fresh Mac. Worth mentioning in the blog post as "the one annoying part"

### 2. The PIN for vLinker FD is 1234, not 0000
- **What happened:** Bluetooth pairing dialog suggests `0000` as default. It doesn't work.
- **The actual PIN:** `1234`
- **Content angle:** Small detail but exactly the kind of thing people Google. Include it prominently.

### 3. The Bluetooth serial port name is `/dev/tty.vLinkerFD-Android`
- **What happened:** Expected something like `vLinkerFD-SerialPort` but the actual name includes "Android" in it — because the adapter is designed primarily for Android devices
- **How to find it:** `ls /dev/tty.*vLinker*`
- **Content angle:** Non-obvious, mention in setup instructions

### 4. Hatchling build backend caused install failures
- **What happened:** Started with `hatchling` as the build backend in pyproject.toml. It's not bundled with Python, so `pip install -e .` fails in a fresh venv with "Cannot import hatchling.backends"
- **The fix:** Switched to `setuptools` which is always available
- **Content angle:** If you're building an MCP server for others to install, use setuptools — don't assume build tools are present

### 5. New setuptools rejects license classifiers + license field together
- **What happened:** PEP 639 superseded the old `License :: OSI Approved :: MIT License` classifier. Having both `license = "MIT"` and the classifier in pyproject.toml causes a build error on latest setuptools
- **The fix:** Remove the license classifier, keep just `license = "MIT"`
- **Content angle:** Skip this in the blog — too in the weeds

### 6. Ctrl+C caused a messy traceback on shutdown
- **What happened:** The MCP server's asyncio event loop didn't handle KeyboardInterrupt cleanly, producing a wall of traceback text plus a "Fatal Python error: _enter_buffered_busy" abort
- **The fix:** Wrap `asyncio.run()` in a `try/except KeyboardInterrupt: pass` and catch `asyncio.CancelledError` in the server coroutine
- **Content angle:** Good engineering detail for the technical blog — shows we thought about UX

### 7. The Folding Top Module reports ZERO fault codes — that's the whole story
- **What happened:** The roof won't operate, but scanning the Folding Top Control Module returns no DTCs. The actual faults (B1310, B166A) are on the Passenger Door Control Unit
- **Why:** The roof controller does a real-time window position check before operating. The window can't move → sequence aborts → but no DTC is stored because it's not a hardware failure, it's a logic check
- **Content angle:** THIS IS THE PUNCHLINE. Claude predicted this before the scan confirmed it. The AI understood the diagnostic logic better than the error codes alone would suggest.

### 8. python-obd doesn't enumerate modules or support MS-CAN out of the box
- **What happened:** The `python-obd` library is great for standard OBD-II PIDs but doesn't natively scan for body/chassis modules on the MS-CAN bus (which is where Ford puts door controllers, roof modules, etc.)
- **Implication for real mode:** The real connection wrapper will need FORScan-like extended scanning or a custom ELM327 command sequence to switch CAN buses
- **For the mock:** We simulate this perfectly — the demo shows all 7 modules including MS-CAN ones
- **Content angle:** Honest limitation. "The mock shows what's possible; real MS-CAN scanning is a harder engineering problem"

### 9. The vLinker FD uses BLE, not classic Bluetooth — serial port is a phantom
- **What happened:** macOS pairs with the vLinker FD and creates `/dev/tty.vLinkerFD-Android`. The port opens without error, but AT commands return empty bytes at every baud rate (500000, 115200, 38400, 9600). Spent hours debugging baud rates, timeouts, reconnects — all dead ends.
- **Root cause:** The vLinker FD is a **BLE (Bluetooth Low Energy)** device. It uses GATT characteristics to tunnel ELM327 commands, not classic Bluetooth SPP. The `/dev/tty` port macOS creates is an RFCOMM channel that the adapter never actually serves data on — it's a phantom.
- **Why it works on iOS:** FORScan on iOS uses Apple's Core Bluetooth framework to talk BLE GATT directly. iOS doesn't even support Bluetooth SPP for third-party apps.
- **The fix:** Use the `bleak` Python library to communicate via BLE GATT. The adapter's service UUID is typically `0000FFF0-*` with write on `FFF2` and notify on `FFF1`. Added `--ble <ADDRESS>` flag to the MCP server.
- **Content angle:** THIS IS GOLD for the blog. "We assumed Bluetooth meant serial port. It didn't. The port existed, opened cleanly, and returned nothing. Hours of debugging baud rates when the entire communication layer was wrong." This is the kind of real engineering story people relate to.

---

## Content Draft Notes

### Hook options (tested in our heads)
1. "My convertible roof broke. So I plugged my car into Claude." — personal, relatable
2. "50 lines of Python to give AI access to your car's brain" — technical, shareable
3. "The fault code that wasn't there" — mystery angle, the folding top insight

### The honest angle works
The AI didn't fix the car. But it understood the diagnostics in a way that would take a human mechanic time to explain. The gap between "reading a code" and "understanding what it means in context" is where AI actually adds value. This is the thesis of the whole piece.

### Key screenshots/recordings needed
- [ ] Terminal: `obd2-mcp --mock` starting up
- [ ] Claude Desktop: asking "scan my car for fault codes" and seeing the tools fire
- [ ] Claude Desktop: the explain_dtc output for B1310 with Focus CC-specific context
- [ ] Claude Desktop: scanning folding_top module and getting zero results
- [ ] Side-by-side: FORScan raw output vs Claude's plain-language explanation
- [ ] Photo: vLinker FD plugged into the Focus CC OBD port
- [ ] Photo: the car with roof down (the one time it worked)

### Numbers for the post
- 7 MCP tools
- 1,937 Ford DTC codes in the database
- 31 automated tests
- 250 lines of server code
- Built and tested in a single session

---

## Technical Deep-Dive: Service Tools & Active Tests

### What OBD-II can and can't discover dynamically

**Dynamically discoverable (any car):**
- Supported PIDs — each ECU responds to "what do you support?" queries (Mode $01 PID $00)
- Active modules — scan CAN addresses, see which ECUs respond
- Stored/pending DTCs — always readable
- Freeze frame data — captured automatically when a DTC is set

**NOT discoverable — requires per-model database:**
- Service tests (e.g. "activate window motor", "cycle roof pump", "flash indicators")
- Active commands (e.g. "unlock door", "run roof open sequence")
- Extended manufacturer PIDs (the deep sensor data FORScan shows)
- Routine Control commands (UDS Service $31) — routine IDs are not self-documenting

### Why FORScan knows so much
FORScan ships with a massive offline database (FRIDA/DRIS — Ford's internal diagnostic reference) that maps every module + model + year to its supported tests, PIDs, and routines. The car itself doesn't advertise this — FORScan looks it up by VIN/module ID.

### What this means for the MCP server
We could add:
1. **Dynamic PID discovery** — ask each module what standard PIDs it supports (works with any car)
2. **Ford Focus CC profile** — a static file with known service tests, extended PIDs, and active commands specific to this car
3. **UDS Routine Control** — run specific tests like "cycle roof sequence" if we know the routine IDs

### Content angle for the blog
"The car doesn't have an API docs page. There's no /help command. The OBD port gives you raw sensor data and fault codes, but the really useful stuff — the service tests, the active commands — lives in Ford's proprietary database. This is the difference between reading a car and talking to it."

---

## What's next
- [ ] Connect to real car and capture actual scan results
- [ ] Add dynamic PID discovery tool
- [ ] Build Ford Focus CC service test profile (if routine IDs can be sourced)
- [ ] Record demo video (screen + car)
- [ ] Write X thread draft
- [ ] Write LinkedIn post draft  
- [ ] Write Medium/dev.to article
- [ ] Open-source on GitHub
- [ ] Submit to Hacker News
