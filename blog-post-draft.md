# I gave Claude access to my car's brain via MCP. It took a Saturday afternoon.

My 2010 Ford Focus CC has a party trick — it's a hardtop convertible. The roof folds into the trunk in about 30 seconds. Or at least it used to, until the passenger window regulator broke. The cable-pulley system inside the door failed, the motor spins but the glass doesn't move, and now the roof refuses to operate entirely. The mechanic fixed the window position in place, the roof worked exactly once (down and up), then refused again.

Nobody could clearly explain why. The roof module isn't throwing any errors. The window is "fixed." But the roof just... won't.

So I did what any developer would do on a Saturday afternoon. I plugged my car into Claude.

## The idea

If you haven't heard of MCP (Model Context Protocol), the short version is: it's a standard for connecting AI models to external tools. Claude can call functions, get data back, and reason about it. People have connected it to databases, APIs, Slack, you name it.

I wanted to connect it to my car's OBD-II diagnostic port — the same port mechanics plug their scan tools into. The idea was simple: build an MCP server that reads fault codes and sensor data from the car, and let Claude interpret what it finds.

The architecture looks almost embarrassingly simple on paper:

```
┌──────────┐    Bluetooth    ┌───────────────┐    Python    ┌──────────────┐    MCP    ┌─────────┐
│   Car    │ ◄─────────────► │  vLinker FD   │ ◄──────────► │  MCP Server  │ ◄───────► │  Claude │
│  OBD-II  │                 │  ($15 dongle) │              │  (250 lines) │           │         │
└──────────┘                 └───────────────┘              └──────────────┘           └─────────┘
```

Four boxes. One protocol. A $15 Bluetooth adapter. How hard could it be?

## The phantom serial port

The first minor annoyance: macOS ships with Python 3.9 (via Xcode tools), but the MCP SDK needs 3.10+. A quick `brew install python@3.12` and we move on. No drama.

The real drama started with the Bluetooth adapter.

I plugged the Vgate vLinker FD into the OBD port, paired it via macOS Bluetooth (PIN is `1234` by the way — not the `0000` the dialog suggests), and checked for the serial port:

```bash
ls /dev/tty.*vLinker*
# /dev/tty.vLinkerFD-Android
```

There it is. Port exists. Let's connect.

```python
import serial
conn = serial.Serial('/dev/tty.vLinkerFD-Android', baudrate=115200, timeout=2)
conn.write(b'ATZ\r')  # Reset ELM327
response = conn.read(100)
print(response)  # b''
```

Nothing. Empty bytes. OK, maybe wrong baud rate. I tried 500000, 38400, 9600. All of them opened cleanly, accepted writes without error, and returned absolutely nothing. The port was a ghost — it existed, opened without complaint, and nobody was home.

I spent a couple of hours on this. Tweaking timeouts, trying different AT commands, reconnecting the adapter, checking if the car needed the ignition on (it did, but that wasn't the issue). Every debugging instinct said "this should work" because the port was right there.

The actual problem? The vLinker FD is a **BLE device** — Bluetooth Low Energy. It uses GATT characteristics to tunnel ELM327 commands, not classic Bluetooth SPP (Serial Port Profile). When macOS pairs with it, it creates an RFCOMM serial port entry out of habit, but the adapter never serves data on that channel. The port is a phantom.

That's why FORScan works fine on iOS — it uses Apple's Core Bluetooth framework to talk BLE directly. It doesn't even try the serial port.

The fix was the `bleak` library — a Python BLE client. The adapter exposes its service on UUID `0000FFF0-*`, with write on characteristic `FFF2` and notify on `FFF1`. Once I pointed the connection at the right abstraction layer, everything lit up immediately.

```python
# The moment it actually worked
async with BleakClient(address) as client:
    await client.start_notify(NOTIFY_UUID, on_response)
    await client.write_gatt_char(WRITE_UUID, b'ATZ\r')
    # "ELM327 v1.5" — finally.
```

The lesson: the hardest part of connecting AI to hardware wasn't the AI part. It was getting bytes from point A to point B. Once the plumbing worked, the rest came together fast.

## Building the MCP server

With the BLE connection sorted, the actual MCP server was almost anticlimactic to write. An MCP tool is just a function with a name, a description, and a JSON schema for inputs. You register it, and Claude can call it.

Here's the core of what a tool looks like:

```python
Tool(
    name="read_dtc",
    description="Read Diagnostic Trouble Codes from the vehicle.",
    inputSchema={
        "type": "object",
        "properties": {
            "module": {
                "type": "string",
                "description": "Specific module to scan, or omit for all."
            }
        }
    }
)
```

That's it. The handler calls `python-obd` (or in our case, the BLE wrapper), reads the codes, and returns JSON. I ended up with 7 tools: `read_dtc`, `clear_dtc`, `get_live_data`, `get_vehicle_info`, `list_modules`, `get_freeze_frame`, and `explain_dtc`. The whole server is about 250 lines of Python.

I also built a mock adapter that simulates my exact car — the broken window, the door module codes, the empty folding top module — so anyone can run the demo without a physical car.

## Scraping the knowledge a car doesn't give you

Here's the thing about OBD-II: when the car returns a code like `B1310`, all you get is a hex string. The car doesn't tell you what it means. The OBD standard doesn't include descriptions. So where do you get that knowledge?

I scraped it. I built an Apify crawler that went through publicly available DTC databases and collected manufacturer-specific codes, fault descriptions, probable causes, and even diagnostic procedures — for over 20 brands. Ford alone gave us 1,937 codes across powertrain, body, and chassis categories. Toyota, BMW, Honda, Hyundai — all of them have their own manufacturer-specific codes that aren't in any standard reference.

The crawler also grabbed diagnostic procedures: how to access codes on older cars without a scan tool, how to clear them, how to run self-tests, what sequence to follow for specific models. All of that got packaged into a JSON database that ships with the MCP server.

This matters because without it, Claude would just see `B1310` and have to guess from its training data. With the scraped database, the `explain_dtc` tool returns the exact fault location, probable cause, and brand-specific context. It's the difference between "some body code" and "Power door unlock circuit failure — check wiring harness at door jamb where flexing causes wear."

Web scraping and AI are a natural pair here. The scraper collects the structured knowledge that cars refuse to self-report, and Claude makes it conversational.

## The moment it clicked

With the server running, I opened Claude Desktop and typed: "Scan my car for fault codes."

Claude called `list_modules`, found 7 ECUs on the bus. Then it called `read_dtc` to scan all of them. The results came back:

**Folding Top Control Module** — 0 DTCs. Clean.

**Passenger Door Control Unit** — 2 DTCs:
- `B1310` — Power door unlock circuit failure
- `B166A` — Heated mirror circuit open

And here's where it got interesting. I asked Claude: "The roof won't operate but the roof module has no fault codes. Why?"

Claude's response nailed it. It explained that the roof controller performs a real-time window position check before initiating the folding sequence. It sends a "lower window slightly" command to the door modules. If the window can't move, the sequence aborts — but no DTC gets stored because it's not a hardware failure in the roof system. It's a precondition check that fails every time.

That's exactly right. The roof module isn't broken. It's working as designed — refusing to operate because it can't confirm the window is clear. The fault codes that matter are on the *door* module, not the roof module.

I had spent weeks being confused by "no fault codes on the roof" before building this. Claude connected the dots in about 10 seconds.

## What it didn't do

Let me be honest: Claude didn't fix my car. The window regulator is still broken. The roof still won't fold. No amount of AI is going to replace a new cable-pulley mechanism and an afternoon with the door panel off.

But here's what it actually did: it made the invisible visible. The gap between "reading a raw fault code" and "understanding what it means in context" is massive. B1310 on its own is just a string. B1310 combined with knowledge of how convertible roof sequencing works, explained in plain language — that's useful.

There's also an honest limitation with the current setup: the OBD-II standard only gives you fault codes and basic sensor data. The really useful stuff — service tests, active commands, extended manufacturer PIDs — lives in Ford's proprietary diagnostic database. The car doesn't have an API docs page. There's no `/help` command. But even with just the standard data, the AI interpretation added genuine value.

## Try it yourself

A disclaimer: I'm not a Python developer. I'm a JavaScript/TypeScript guy who vibe-coded his way through this with Claude's help. The code works, but don't expect production-grade Python. This is very much an alpha — it's meant as inspiration and a starting point, not something you should rely on for actual car diagnostics. If it inspires you to build something better, that's the whole point.

The repo is open source: [github.com/petrpatek/obd2-mcp-server](https://github.com/petrpatek/obd2-mcp-server)

```bash
git clone https://github.com/petrpatek/obd2-mcp-server.git
cd obd2-mcp-server
./setup.sh
source .venv/bin/activate
obd2-mcp --mock  # No car needed
```

Add it to Claude Desktop's config and you'll have 7 car diagnostic tools available. The mock simulates the Focus CC scenario with real fault codes.

If you have any ELM327-compatible adapter, swap `--mock` for `--ble` (or `--port` for USB adapters) and point it at a real car.

## What's next (and what I'm deliberately NOT building)

The database already includes diagnostic procedures for each brand — step-by-step instructions for running self-tests, resetting service intervals, cycling actuators. In theory, you could expose these as MCP tools: "run the roof sequence self-test", "cycle the window motor", "activate the fuel pump."

I'm not doing that. At least not yet.

Here's why: I'm a developer, not a car mechanic. Reading fault codes is safe — it's passive, you're just asking the car what it already knows. But sending active commands to modules? That's a different game. A wrong routine ID, a command sent at the wrong time, or an actuator test while someone's hand is near a moving part — that's how you break things or hurt people. The gap between "technically possible via UDS Service $31" and "safe to let AI trigger autonomously" is enormous.

What I *am* working on: dynamic PID discovery (asking each ECU what sensors it supports), live data streaming, and better brand coverage for the DTC database. The read-only side of car diagnostics is genuinely useful and genuinely safe. The write side needs a lot more guardrails before it belongs in an open-source tool.

But the bigger takeaway for me is how trivially MCP bridges the physical world to AI. The hard part wasn't Claude, wasn't the protocol, wasn't even the tool definitions. It was figuring out that my "Bluetooth" adapter speaks BLE over GATT, not serial over RFCOMM. Once the bytes flowed, everything else took an afternoon.

If you have hardware that speaks a protocol — OBD-II, MQTT, Modbus, whatever — MCP can bridge it to AI in a day. The barrier isn't technical anymore. It's just knowing which wire to pull.
