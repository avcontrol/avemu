# Agent Instructions

See **[../AGENTS.md](../AGENTS.md)** for shared instructions across all avcontrol projects.

---

## Project Overview

**avemu** is a device emulator for A/V equipment protocols. It enables testing Home Assistant integrations and pyavcontrol without physical hardware.

---

## Architecture

| Component | Purpose |
|-----------|---------|
| Emulator core | Protocol simulation engine |
| Device profiles | YAML-based device behavior definitions |
| Transport layer | RS-232/TCP connection handling |

---

## Development

### Running the Emulator

```bash
uv sync
uv run avemu mcintosh/mx160
```

### Adding Device Emulation

Device emulation profiles should match the YAML structure in `avcontrol/pyavcontrol/protocols/`. The emulator uses:

- Command patterns from YAML
- Response templates
- State machine definitions

### Testing

```bash
uv run pytest tests/ -v
```

---

## Quality Gates

Before committing:

```bash
uv run pytest tests/ -v
uv run ruff check .
uv run mypy .
```
