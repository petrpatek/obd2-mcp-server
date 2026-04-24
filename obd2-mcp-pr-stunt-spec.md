# "I Let AI Diagnose My Car" — PR Stunt Specification

## Concept

A real-world demonstration of connecting Claude AI to a car's OBD-II diagnostic port via MCP (Model Context Protocol). The story follows a genuine problem — a 2010 Ford Focus CC convertible with a broken passenger window blocking the roof mechanism — and shows how AI could read, interpret, and troubleshoot car fault codes in natural language.

**Key narrative hook:** This actually happened. The AI didn't magically fix the car, but it understood the diagnostics better than most mechanics explain them. The honest angle ("it didn't fix anything, but here's why that's still impressive") makes it authentic and shareable.

---

## The Real Story (Content Foundation)

### The Problem
- 2010 Ford Focus CC convertible — passenger window regulator broken (cable/pulley system failed)
- Window mechanically fixed in place, motor runs but glass doesn't move
- Convertible roof refuses to operate because it requires all windows to respond
- After service fixed the window position, roof worked exactly once (down + up), then refused again

### The Diagnostic Journey
- Connected Vgate vLinker FD (Bluetooth OBD-II adapter) to the car
- Used FORScan Lite to scan all modules
- Found: Folding Top Control Module — no stored DTCs
- Found: Passengers Door Control Unit — B1310 (Power door unlock circuit failure) + B166A (Heated Mirror Circuit Open)
- Diagnosis: The roof system sends a "lower window slightly" command before operating. The window can't move, so the sequence aborts — but doesn't store a permanent fault code because it's a real-time check, not a hardware failure

### The Insight
The AI (Claude) correctly predicted the diagnosis before the scan was done. It identified that the lack of stored fault codes pointed to a real-time position check rather than a stored error, and suggested the exact diagnostic tool and approach that confirmed the theory.

---

## Technical Architecture

### What We're Building

An MCP server (`obd2-mcp-server`) that bridges a car's OBD-II port to Claude via the Model Context Protocol.

```
┌──────────┐     Bluetooth/USB     ┌───────────────┐     python-obd     ┌──────────────┐     MCP (stdio)     ┌─────────┐
│   Car    │ ◄──────────────────► │  vLinker FD   │ ◄────────────────► │  MCP Server  │ ◄────────────────► │  Claude  │
│  OBD-II  │                      │  (Adapter)    │                    │  (Python)    │                    │   AI    │
└──────────┘                      └───────────────┘                    └──────────────┘                    └─────────┘
```

### MCP Server Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `read_dtc` | Read Diagnostic Trouble Codes | `module` (optional — all modules if omitted) |
| `clear_dtc` | Clear stored fault codes | `module` (required), `confirm` (boolean) |
| `get_live_data` | Read real-time sensor values | `pids` (list of PIDs to read) |
| `get_vehicle_info` | Read VIN, calibration IDs, etc. | none |
| `list_modules` | List all ECUs on the bus | none |
| `get_freeze_frame` | Snapshot of data when DTC was set | `dtc_code` |
| `explain_dtc` | AI-enhanced: explain a DTC in plain language | `dtc_code` |

### Tech Stack

- **Language:** Python 3.10+
- **OBD library:** `python-obd` (ELM327 compatible, supports HS-CAN and MS-CAN)
- **MCP SDK:** `mcp` Python package (Anthropic's official SDK)
- **Adapter:** Vgate vLinker FD (Bluetooth 4.0 / USB) — or any ELM327-compatible adapter
- **Platform:** macOS / Linux / Windows

### Repository Structure

```
obd2-mcp-server/
├── README.md
├── pyproject.toml
├── src/
│   └── obd2_mcp/
│       ├── __init__.py
│       ├── server.py          # MCP server entry point
│       ├── obd_connection.py  # python-obd wrapper
│       ├── dtc_database.py    # DTC code descriptions
│       └── tools/
│           ├── read_dtc.py
│           ├── clear_dtc.py
│           ├── live_data.py
│           └── vehicle_info.py
├── tests/
│   ├── test_server.py
│   └── mock_obd.py           # Mock adapter for demos
└── demo/
    ├── screenshots/           # FORScan vs Claude comparison
    └── demo_script.md         # Step-by-step demo walkthrough
```

### Mock Mode (for demos without a car)

The server includes a mock adapter that simulates a Ford Focus CC with the exact fault codes from the real story (B1310, B166A). This allows demos, conference talks, and video recordings without needing a physical car.

---

## Content Strategy

### Narrative Arc

1. **Hook:** "My convertible roof stopped working. So I plugged my car into ChatGPT's competitor."
2. **Setup:** The real problem — window broke, roof won't move, mechanic can't explain why
3. **The Build:** 50 lines of Python to connect a car to AI via MCP
4. **The Demo:** Claude reads real fault codes, explains them in plain language, suggests next steps
5. **The Honest Twist:** "It didn't fix the car. Here's why that's actually the point."
6. **The Vision:** What this means for the future of car ownership

### Key Messages

- MCP makes AI integration with physical hardware trivially simple
- AI doesn't replace mechanics — it translates car-speak to human-speak
- The gap between "reading a fault code" and "understanding what it means" is exactly where AI excels
- This took a weekend to build. Imagine what OEMs could do.

---

## Platform-Specific Content

### X (Twitter) — Thread (8-10 tweets)

**Goal:** Virality, developer engagement, quote-tweet potential

**Thread structure:**

1. Hook tweet with photo of vLinker plugged into car + terminal showing Claude output
2. "Here's the problem..." (convertible roof story, 1-2 tweets)
3. "So I built this..." (architecture diagram, 1 tweet)
4. "50 lines of Python later..." (code screenshot, 1 tweet)
5. Demo video/GIF — Claude reading live fault codes (1-2 tweets)
6. "It didn't fix anything. And that's the point." (honest take, 1 tweet)
7. "The code is open source" + link (1 tweet)
8. Call to action: "What hardware would you connect to AI?" (engagement bait)

**Timing:** Tuesday or Wednesday, 9-10am EST (peak dev Twitter)

### LinkedIn — Post

**Goal:** Professional reach, thought leadership, reshares from auto/tech industry people

**Format:** Personal story post (no article — native posts get 5-10x more reach)

**Structure:**
- Open with a personal hook ("My car's convertible roof broke last month...")
- Short version of the story (3-4 paragraphs)
- Technical insight without too much jargon
- End with a forward-looking take on AI + physical world integration
- Include 3-4 images: car, adapter, terminal output, architecture diagram

**Hashtags (3-5):** #AI #MCP #Automotive #OpenSource #Claude

### Medium — Long-form Article

**Goal:** SEO, permanence, deep technical credibility, cross-link from X/LinkedIn

**Title options:**
1. "I Connected My Car to AI — Here's What Happened"
2. "50 Lines of Python: How I Gave Claude AI Access to My Car's Brain"
3. "My Broken Convertible Taught Me the Future of AI Isn't Software"

**Structure (2,000-2,500 words):**
1. The Story (500w) — engaging, personal, the real problem
2. The Idea (300w) — what is MCP, why it matters for hardware
3. The Build (600w) — technical walkthrough with code snippets
4. The Demo (400w) — what Claude actually said, screenshots
5. The Honest Take (300w) — limitations, what it can't do
6. The Future (400w) — vision for AI + automotive, call to open source

**Publish on:** Medium + cross-post to dev.to (canonical URL on Medium)

---

## Visual Assets Needed

| Asset | Format | Used On | Description |
|-------|--------|---------|-------------|
| Hero photo | JPG | All | vLinker FD plugged into OBD port of the Focus CC |
| Architecture diagram | SVG/PNG | All | Car → Adapter → MCP Server → Claude flow |
| Terminal screenshot | PNG | X, Medium | Claude reading DTCs in real-time |
| Code snippet | PNG | X | The core 50 lines of the MCP server |
| Before/After | PNG | LinkedIn, Medium | FORScan raw output vs Claude's explanation |
| Demo video | MP4 (30-60s) | X, LinkedIn | Screen recording of live diagnosis |
| Car photo | JPG | Medium | The actual Focus CC with roof down |

---

## Prototype Build Plan

### Phase 1: Core MCP Server (Day 1-2)
- [ ] Set up Python project with `mcp` SDK
- [ ] Implement `python-obd` connection wrapper
- [ ] Build `read_dtc` tool — scan all modules, return codes with descriptions
- [ ] Build `list_modules` tool — enumerate available ECUs
- [ ] Build `get_vehicle_info` tool — VIN, model info
- [ ] Build mock adapter for demo mode
- [ ] Test with vLinker FD on the actual car

### Phase 2: Enhanced Tools (Day 3)
- [ ] Build `get_live_data` tool — real-time sensor readings
- [ ] Build `clear_dtc` tool — with safety confirmation
- [ ] Build `get_freeze_frame` tool
- [ ] Add MS-CAN support (needed for Ford body modules like the roof)

### Phase 3: Content Production (Day 4-5)
- [ ] Record demo video on actual car
- [ ] Take all photos (adapter, car, terminal)
- [ ] Create architecture diagram
- [ ] Write X thread draft
- [ ] Write LinkedIn post draft
- [ ] Write Medium article draft

### Phase 4: Launch (Day 6)
- [ ] Open-source the repo on GitHub
- [ ] Publish Medium article
- [ ] Post LinkedIn
- [ ] Post X thread (link to Medium + GitHub)
- [ ] Cross-post to dev.to and Hacker News

---

## Traction Potential Assessment

### X (Twitter)
**Potential: HIGH.** Dev Twitter loves "I connected X to Y" stories. The car angle is unusual enough to stand out from typical AI demos. The honest "it didn't fix anything" angle prevents the usual backlash against AI hype. Realistic target: 500-2K likes if the thread is well-crafted and the demo video is compelling. Could go higher if picked up by AI/MCP community accounts.

### LinkedIn
**Potential: MEDIUM-HIGH.** LinkedIn rewards personal stories with professional insight. The automotive + AI crossover appeals to multiple audiences (tech, automotive, innovation). The story format (not corporate jargon) will outperform typical LinkedIn content. Realistic target: 5K-20K impressions, 200-500 reactions.

### Medium
**Potential: MEDIUM.** Medium's algo favors publication placement. Consider submitting to "Towards Data Science", "Better Programming", or "The Startup" for distribution. SEO value is the real play here — "MCP server OBD-II" will rank with zero competition. Realistic target: 2K-5K reads in first month, long-tail SEO traffic after.

### Hacker News (Bonus)
**Potential: HIGH if it hits front page.** HN loves novel hardware+software projects, especially open-source ones with a real use case. The "weekend project" vibe fits perfectly. Title suggestion: "Show HN: I built an MCP server that lets Claude diagnose my car via OBD-II"

### Risk Factors
- **"AI replacing mechanics" backlash** — mitigate with the honest angle (it doesn't replace, it translates)
- **"Just a wrapper" criticism** — address by showing the MS-CAN complexity and real-time data interpretation
- **Technical credibility** — open-source code + real car demo video eliminates "vaporware" accusations

---

## Success Metrics

| Metric | Target | Stretch |
|--------|--------|---------|
| GitHub stars (week 1) | 200 | 1,000 |
| X thread impressions | 50K | 200K |
| LinkedIn impressions | 10K | 50K |
| Medium reads | 2K | 10K |
| HN front page | Top 30 | Top 10 |
| Media/blog pickups | 1 | 3+ |

---

## Open Questions

1. **Branding:** Ship under personal name or create a project name? (e.g., "CarGPT", "MechanicAI", "obd2-mcp")
2. **Timing:** Coordinate with any Anthropic MCP announcements for amplification?
3. **Follow-up content:** Series potential? (Part 2: "I actually fixed the car", Part 3: "Real-time dashboard")
4. **Language:** English only, or Czech version for local tech media?
