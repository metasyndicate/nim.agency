"""
Config - Source-controlled defaults for NIM Agency.

Loads etc/agency.json from the project root. The file is treated as
read-only at runtime; operator overrides come from CLI flags.
Falls back to built-in defaults if the file is missing or invalid.
"""

import json
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "etc" / "agency.json"

DEFAULTS = {
    "dispatch": {
        "provider": "claude-cli",
        "max_concurrent": 2,
        "timeout_seconds": 600,
        "report_dir": "log",
        "modality": "readonly",
    },
    "logging": {
        "dir": "~/.nim/agency",
        "dispatch_csv": "dispatch.log",
        "dispatch_json": "dispatch.json",
    },
    "economy": {
        "xp_per_task": 25,
        "gold_per_task": 10,
        "mission_bonus_xp": 15,
        "mission_bonus_gold": 5,
        "skill_points_per_rank": 10,
        "max_tool_proficiency": 3,
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


# Global dispatch posture, most restrictive first. One knob controls what
# the whole agency may do; per-mission constraints can only narrow it.
#   readonly     - missions must be read-only and local (dev/test/train)
#   supervised   - write-capable local missions allowed where a project
#                  policy or template explicitly asks for them
#   unrestricted - remote missions also allowed (inert until phase D
#                  access grants exist)
MODALITIES = ("readonly", "supervised", "unrestricted")


def modality() -> str:
    """The agency's global dispatch posture (etc/agency.json)."""
    value = get_config()["dispatch"].get("modality", "readonly")
    return value if value in MODALITIES else "readonly"


def modality_violation(read_only: bool, local_only: bool) -> Optional[str]:
    """Why the global modality refuses these mission constraints, or None.
    Checked at briefing time and again by the dispatcher as a backstop."""
    posture = modality()
    if posture == "readonly" and not read_only:
        return ("global modality 'readonly' forbids write-capable missions "
                "- set dispatch.modality to 'supervised' in etc/agency.json")
    if posture in ("readonly", "supervised") and not local_only:
        return (f"global modality '{posture}' forbids remote missions - "
                "requires 'unrestricted' and phase D access grants")
    return None


def log_dir() -> Path:
    """Resolved operator-local log directory (default: ~/.nim/agency)."""
    return Path(get_config()["logging"]["dir"]).expanduser()


def report_dir() -> Path:
    """Resolved mission report directory (default: <project_root>/log)."""
    path = Path(get_config()["dispatch"]["report_dir"])
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path
