# Contributing

This project is alpha-quality and welcomes contributions.

## Getting started

```bash
./setup.sh
source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src/ tests/
```

## Notes

- Python 3.10+ required (MCP SDK dependency)
- Run `obd2-mcp --mock` to test without a car
- BLE support requires `pip install -e ".[ble]"`
