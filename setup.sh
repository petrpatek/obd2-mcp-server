#!/usr/bin/env bash
#
# obd2-mcp-server setup script
# Works on macOS and Linux. Finds or installs Python 3.10+,
# creates a venv, installs the package, and prints next steps.
#

set -euo pipefail

MIN_MAJOR=3
MIN_MINOR=10
VENV_DIR=".venv"

# ── Colors (if terminal supports them) ──────────────────────────────────────

if [ -t 1 ]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  CYAN='\033[0;36m'
  BOLD='\033[1m'
  NC='\033[0m'
else
  RED='' GREEN='' YELLOW='' CYAN='' BOLD='' NC=''
fi

info()  { echo -e "${CYAN}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
fail()  { echo -e "${RED}✘${NC}  $*"; exit 1; }

# ── Find a suitable Python ──────────────────────────────────────────────────

find_python() {
  # Try these in order: python3.13, python3.12, python3.11, python3.10, python3
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
      local ver
      ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || continue
      local major minor
      major=$(echo "$ver" | cut -d. -f1)
      minor=$(echo "$ver" | cut -d. -f2)
      if [ "$major" -ge "$MIN_MAJOR" ] && [ "$minor" -ge "$MIN_MINOR" ]; then
        PYTHON_BIN=$(command -v "$candidate")
        PYTHON_VER="$ver"
        return 0
      fi
    fi
  done
  return 1
}

# ── Install Python if missing ───────────────────────────────────────────────

install_python() {
  echo ""
  warn "No Python >= ${MIN_MAJOR}.${MIN_MINOR} found on this system."
  echo ""

  OS="$(uname -s)"
  case "$OS" in
    Darwin)
      if command -v brew &>/dev/null; then
        info "Installing Python 3.12 via Homebrew..."
        brew install python@3.12
        PYTHON_BIN=$(brew --prefix python@3.12)/bin/python3.12
        PYTHON_VER="3.12"
      else
        echo ""
        echo "  Install options for macOS:"
        echo ""
        echo "    ${BOLD}Option 1: Homebrew (recommended)${NC}"
        echo "      /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "      brew install python@3.12"
        echo "      # then re-run: ./setup.sh"
        echo ""
        echo "    ${BOLD}Option 2: python.org installer${NC}"
        echo "      https://www.python.org/downloads/"
        echo ""
        fail "Please install Python >= ${MIN_MAJOR}.${MIN_MINOR} and re-run this script."
      fi
      ;;
    Linux)
      if command -v apt-get &>/dev/null; then
        info "Installing Python 3.12 via apt..."
        sudo apt-get update
        sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
        PYTHON_BIN=$(command -v python3.12)
        PYTHON_VER="3.12"
      elif command -v dnf &>/dev/null; then
        info "Installing Python 3.12 via dnf..."
        sudo dnf install -y python3.12
        PYTHON_BIN=$(command -v python3.12)
        PYTHON_VER="3.12"
      elif command -v pacman &>/dev/null; then
        info "Installing Python via pacman..."
        sudo pacman -S --noconfirm python
        PYTHON_BIN=$(command -v python3)
        PYTHON_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
      else
        echo ""
        echo "  Could not detect a supported package manager (apt, dnf, pacman)."
        echo "  Please install Python >= ${MIN_MAJOR}.${MIN_MINOR} manually:"
        echo "    https://www.python.org/downloads/"
        echo ""
        fail "Please install Python >= ${MIN_MAJOR}.${MIN_MINOR} and re-run this script."
      fi
      ;;
    *)
      fail "Unsupported OS: $OS. Please install Python >= ${MIN_MAJOR}.${MIN_MINOR} manually."
      ;;
  esac
}

# ── Main ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}🔧 obd2-mcp-server setup${NC}"
echo ""

cd "$(dirname "$0")"

# Step 1: Find or install Python
info "Looking for Python >= ${MIN_MAJOR}.${MIN_MINOR}..."

if find_python; then
  ok "Found ${PYTHON_BIN} (${PYTHON_VER})"
else
  install_python
  # Verify it worked
  if ! find_python; then
    fail "Python installation succeeded but could not find a working binary. Check your PATH."
  fi
  ok "Installed Python ${PYTHON_VER}"
fi

# Step 2: Create venv
if [ -d "$VENV_DIR" ]; then
  info "Removing existing venv..."
  rm -rf "$VENV_DIR"
fi

info "Creating virtual environment..."
"$PYTHON_BIN" -m venv "$VENV_DIR"
ok "Virtual environment created at ${VENV_DIR}/"

# Step 3: Activate and install
info "Installing obd2-mcp-server..."
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip --quiet
pip install -e . 2>&1 | tail -3
ok "Installed successfully"

# Step 4: Verify
if command -v obd2-mcp &>/dev/null; then
  ok "obd2-mcp command is available"
else
  warn "obd2-mcp not on PATH — use: ${VENV_DIR}/bin/obd2-mcp"
fi

# Step 5: Print next steps
FULL_PATH="$(cd "$(dirname "$0")" && pwd)/${VENV_DIR}/bin/obd2-mcp"
echo ""
echo -e "${BOLD}✅ Setup complete!${NC}"
echo ""
echo "  Run in demo mode:"
echo -e "    ${CYAN}source ${VENV_DIR}/bin/activate${NC}"
echo -e "    ${CYAN}obd2-mcp --mock${NC}"
echo ""
echo "  Or with a real OBD adapter:"
echo -e "    ${CYAN}obd2-mcp --port /dev/tty.vLinkerFD-SerialPort${NC}"
echo ""
echo "  Claude Desktop config (claude_desktop_config.json):"
echo -e "    ${CYAN}{\"mcpServers\": {\"obd2\": {\"command\": \"${FULL_PATH}\", \"args\": [\"--mock\"]}}}${NC}"
echo ""
