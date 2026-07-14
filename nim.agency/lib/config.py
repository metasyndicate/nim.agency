"""
Config - Source-controlled defaults for NIM Agency.

Loads etc/agency.json from the project root. The file is treated as
read-only at runtime; operator overrides come from CLI flags.
Falls back to built-in defaults if the file is missing or invalid.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "etc" / "agency.json"

DEFAULTS = {
    "dispatch": {
        "provider": "claude-cli",
        "max_concurrent": 2,
        "timeout_seconds": 600,
        "report_dir": "log",
    },
    "logging": {
        "dir": "~/.nim/agency",
        "dispatch_csv": "dispatch.log",
        "dispatch_json": "dispatch.json",
    },
}

_config = None


def get_config() -> dict:
    """Load and cache config, merging etc/agency.json over built-in defaults."""
    global _config
    if _config is None:
        merged = {section: dict(values) for section, values in DEFAULTS.items()}
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
            for section, values in data.items():
                if section in merged and isinstance(values, dict):
                    merged[section].update(values)
        except (OSError, json.JSONDecodeError):
            pass
        _config = merged
    return _config


def log_dir() -> Path:
    """Resolved operator-local log directory (default: ~/.nim/agency)."""
    return Path(get_config()["logging"]["dir"]).expanduser()


def report_dir() -> Path:
    """Resolved mission report directory (default: <project_root>/log)."""
    path = Path(get_config()["dispatch"]["report_dir"])
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path
