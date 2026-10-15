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
        "provider": "claude_cli",
        "max_concurrent": 2,
        "timeout_seconds": 600,
        "report_dir": "log",
        "modality": "readonly",
        "egress_policy": "public",
    },
    "harness": {
        "max_iterations": 12,
        "max_calls_per_turn": 8,
        "call_timeout_seconds": 30,
        "max_result_chars": 16000,
        "token_budget": 200000,
    },
    "validation": {
        "default": "enforce",
        "entities": {},
    },
    "install": {
        "home": None,         # None: resolved (lib/install/paths.py)
    },
    "relay": {
        "base_delay_seconds": 1.0,
        "max_delay_seconds": 20.0,
        "max_retransmits_per_task": 4,
    },
    "logging": {
        "dir": None,          # None: the instance home (lib/install/paths.py)
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


# Schema validation on datastore writes, per entity. Modes follow
# Kubernetes Pod Security Admission: enforce refuses a non-conforming
# write; warn writes it and records the errors. warn exists so policy can
# be switched on before every stored record conforms (it is a migration
# state, not a destination). Without jsonschema installed nothing is
# validated; `nim.agency data check` says so.
VALIDATION_MODES = ("enforce", "warn")


def validation_mode(entity: str) -> str:
    """How a write of this entity treats schema errors (etc/agency.json)."""
    section = get_config().get("validation", {})
    value = (section.get("entities") or {}).get(entity, section.get("default"))
    return value if value in VALIDATION_MODES else "enforce"


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


# How far prompt content may travel, least to most exposed. Independent of
# MODALITIES: modality governs what a mission may DO, egress governs where
# its prompts GO. A read-only local mission still ships its prompts (and
# whatever the harness reads) to whichever syndicate serves it.
#   local   - only syndicates on this host
#   private - also operator-controlled networks (tailnet, LAN)
#   public  - also third-party APIs (the default; claude_cli is public)
EGRESS_POLICIES = ("local", "private", "public")


def egress_policy() -> str:
    """How far prompt content may travel (etc/agency.json)."""
    value = get_config()["dispatch"].get("egress_policy", "public")
    return value if value in EGRESS_POLICIES else "public"


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
    """Resolved operator-local log directory: logging.dir when set, else
    the instance home (lib/install/paths.py)."""
    configured = get_config()["logging"].get("dir")
    if configured:
        return Path(configured).expanduser()
    from .install import home
    return home()


def report_dir() -> Path:
    """Resolved mission report directory (default: <project_root>/log)."""
    path = Path(get_config()["dispatch"]["report_dir"])
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path
