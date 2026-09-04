"""
Mugshots - ASCII portrait loading and blending.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import random
from pathlib import Path

class MugshotLoader:
    """Load ASCII art portraits from the mugshots directory."""

    # Map rank slugs to mugshot pools (diverse options per rank tier)
    RANK_MUGSHOTS = {
        # Junior tier - fresh faces, eager rookies
        "grunt": ["grunt_01", "rookie", "junior", "wirehead", "analyst"],
        "tinkerer": ["tinkerer_01", "junior", "rookie", "wirehead", "developer"],
        # Mid tier - competent operators
        "operator": ["operator_01", "operator_02", "operator", "analyst2", "developer"],
        "ranger": ["ranger_01", "operator", "analyst3", "security", "dispatch"],
        # Senior tier - experienced specialists
        "synthesizer": ["synthesizer_01", "senior", "analyst", "shaman", "uiux"],
        "theorist": ["theorist_01", "senior", "shaman", "owl", "security"],
        # Elite tier - legendary operators
        "neckbeard": ["neckbeard_01", "owl", "shaman", "hq", "dispatch"],
        "berserker": ["berserker_01", "owl", "hq", "shaman", "security"],
    }

    # Archetype-based pools for thematic matching (used as secondary selection)
    ARCHETYPE_MUGSHOTS = {
        "linux_admin": ["operator", "operator_01", "dispatch", "security"],
        "devops_engineer": ["developer", "operator", "dispatch", "wirehead"],
        "security_analyst": ["security", "analyst", "wirehead", "owl"],
        "network_engineer": ["wirehead", "operator", "dispatch", "security"],
        "dba": ["analyst", "analyst2", "analyst3", "senior"],
        "cloud_engineer": ["developer", "dispatch", "uiux", "operator"],
        "backend_dev": ["developer", "wirehead", "analyst", "uiux"],
        "data_engineer": ["analyst", "analyst2", "analyst3", "developer"],
        "platform_engineer": ["dispatch", "operator", "developer", "hq"],
        "sre": ["operator", "dispatch", "security", "owl"],
    }

    # All available mugshots for fallback/random selection
    ALL_MUGSHOTS = [
        "grunt_01", "tinkerer_01", "operator_01", "operator_02", "ranger_01",
        "synthesizer_01", "theorist_01", "neckbeard_01", "berserker_01",
        "analyst", "analyst2", "analyst3", "developer", "uiux",
        "security", "wirehead", "shaman", "owl", "dispatch", "hq",
        "senior", "junior", "rookie", "operator"
    ]

    _cache: dict[str, list[str]] = {}
    _mugshots_path: Path = None

    @classmethod
    def get_path(cls) -> Path:
        if cls._mugshots_path is None:
            cls._mugshots_path = Path(__file__).parent / "mugshots"
        return cls._mugshots_path

    @classmethod
    def list_available(cls) -> list[str]:
        """List all available mugshot names."""
        path = cls.get_path()
        if not path.exists():
            return []
        return [f.stem for f in path.glob("*.txt")]

    @classmethod
    def load(cls, name: str) -> list[str]:
        """Load a mugshot by name, returning list of lines."""
        if name in cls._cache:
            return cls._cache[name]

        filepath = cls.get_path() / f"{name}.txt"
        if not filepath.exists():
            filepath = cls.get_path() / "unknown.txt"

        lines = []
        if filepath.exists():
            with open(filepath, 'r') as f:
                lines = [line.rstrip() for line in f.readlines()]

        cls._cache[name] = lines
        return lines

    @classmethod
    def get_for_rank(cls, rank_slug: str, agent_id: str) -> list[str]:
        """Get a mugshot appropriate for the rank, deterministic by agent_id."""
        import hashlib
        options = cls.RANK_MUGSHOTS.get(rank_slug, cls.ALL_MUGSHOTS)
        # Use agent_id to deterministically pick one
        idx = int(hashlib.md5(agent_id.encode()).hexdigest(), 16) % len(options)
        return cls.load(options[idx])

    @classmethod
    def get_blended(cls, rank_slug: str, archetype_slug: str, agent_id: str) -> list[str]:
        """Mugshot from the merged rank + archetype pools, deterministic
        per agent. Takes resolved slugs so callers with reference-table
        access (the dossier) get archetype-flavored portraits."""
        import hashlib

        combined = list(cls.RANK_MUGSHOTS.get(rank_slug, []))
        for opt in cls.ARCHETYPE_MUGSHOTS.get(archetype_slug, []):
            if opt not in combined:
                combined.append(opt)
        if not combined:
            combined = cls.ALL_MUGSHOTS

        idx = int(hashlib.md5(agent_id.encode()).hexdigest(), 16) % len(combined)
        return cls.load(combined[idx])

    @classmethod
    def get_for_agent(cls, agent: dict) -> list[str]:
        """
        Get a mugshot for an agent, considering rank and archetype.

        Uses a blend of rank-based and archetype-based selection for variety.
        """
        import hashlib

        agent_id = agent.get("id", "unknown")
        cls_data = agent.get("classification", {})
        rank_slug = cls_data.get("rank_slug", "grunt")
        archetype_slug = cls_data.get("archetype_slug", "")

        # Build combined pool: rank options + archetype options
        rank_options = cls.RANK_MUGSHOTS.get(rank_slug, [])
        arch_options = cls.ARCHETYPE_MUGSHOTS.get(archetype_slug, [])

        # Merge pools, preferring rank but adding archetype variety
        combined = list(rank_options)
        for opt in arch_options:
            if opt not in combined:
                combined.append(opt)

        if not combined:
            combined = cls.ALL_MUGSHOTS

        # Deterministic selection based on agent_id
        idx = int(hashlib.md5(agent_id.encode()).hexdigest(), 16) % len(combined)
        return cls.load(combined[idx])


