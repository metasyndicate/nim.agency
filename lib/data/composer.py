"""
Agent Composer - Assembles complete operational profiles for agentic dispatch

Takes an agent entity, resolves their archetype and instructions,
and composes a complete operational profile including:
- System prompt with role, constraints, and decision framework
- Capability matrix with proficiency levels
- Tool access list with risk metadata
- Rendered instruction payload ready for LLM consumption

Supports composable instruction blocks that are merged by priority:
- Rank modifiers (novice, journeyman, expert, master)
- Focus overlays (firewall, kubernetes, database, etc.)
- Scope definitions (read_only, supervised, autonomous)
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from textwrap import dedent, indent

from .crud import DataStore, get_datastore


@dataclass
class ToolAccess:
    """Tool with access permissions and risk metadata."""
    slug: str
    label: str
    description: str
    risk_level: str
    requires_confirmation: bool
    reversibility: str
    scope: str
    dangerous_subcommands: list[str] = field(default_factory=list)
    whispers: list[str] = field(default_factory=list)


@dataclass
class CapabilityLevel:
    """Agent's proficiency in a capability."""
    slug: str
    label: str
    category: str
    proficiency: int
    proficiency_desc: str
    unlocked_tools: list[str] = field(default_factory=list)


@dataclass
class InstructionBlock:
    """A composable instruction block that modifies agent behavior."""
    id: str
    slug: str
    label: str
    category: str  # rank, focus, scope, persona, context, expertise
    priority: int  # 0-100, higher priority blocks override lower
    applies_when: dict  # Conditions for this block to apply
    blocks: dict  # The actual instruction content
    meta: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "InstructionBlock":
        """Create from JSON dict."""
        return cls(
            id=data.get("id", ""),
            slug=data.get("slug", ""),
            label=data.get("label", ""),
            category=data.get("category", ""),
            priority=data.get("priority", 50),
            applies_when=data.get("applies_when", {}),
            blocks=data.get("instruction_blocks", {}),
            meta=data.get("meta", {})
        )


@dataclass
class OperationalProfile:
    """Complete operational profile for an agent."""
    # Identity
    agent_id: str
    agent_name: str
    agent_codename: str
    role_title: str

    # Classification
    archetype_slug: str
    archetype_label: str

    # Instructions
    preamble: str
    primary_role: str
    secondary_roles: list[str]
    capabilities_desc: list[str]
    constraints: list[str]
    decision_framework: dict
    communication_style: dict

    # Operational
    capabilities: list[CapabilityLevel]
    tools: list[ToolAccess]
    tool_risk_overrides: dict

    # Team context
    pairs_well_with: list[str]
    team_size: dict

    # Provenance: instruction sources (reference slugs and file paths) that
    # contributed to this profile
    sources: list[str] = field(default_factory=list)

    def render_system_prompt(self) -> str:
        """Render the complete system prompt for LLM consumption."""
        sections = []

        # Identity
        sections.append(f"# AGENT IDENTITY\n\n{self.preamble}")
        sections.append(f"**Designation:** {self.agent_name} ({self.agent_codename})")
        sections.append(f"**Role:** {self.role_title}")
        sections.append(f"**Archetype:** {self.archetype_label}")

        # Role
        sections.append("\n# PRIMARY MISSION\n")
        sections.append(self.primary_role)

        if self.secondary_roles:
            sections.append("\n## Secondary Objectives\n")
            for role in self.secondary_roles:
                sections.append(f"- {role}")

        # Capabilities
        sections.append("\n# CAPABILITIES\n")
        sections.append("You are authorized to perform the following actions:\n")
        for cap in self.capabilities_desc:
            sections.append(f"- {cap}")

        # Constraints - THE IMPORTANT PART
        sections.append("\n# OPERATIONAL CONSTRAINTS\n")
        sections.append("**CRITICAL: You MUST adhere to these constraints at all times:**\n")
        for constraint in self.constraints:
            sections.append(f"- {constraint}")

        # Decision Framework
        sections.append("\n# DECISION FRAMEWORK\n")
        df = self.decision_framework
        if df.get("investigate_first"):
            sections.append(f"**Before Acting:** {df['investigate_first']}")
        if df.get("reversibility"):
            sections.append(f"**Reversibility:** {df['reversibility']}")
        if df.get("blast_radius"):
            sections.append(f"**Impact Assessment:** {df['blast_radius']}")
        if df.get("escalation"):
            sections.append(f"**Escalation Criteria:** {df['escalation']}")

        # Tool Access
        sections.append("\n# TOOL ACCESS\n")
        sections.append("You have access to the following tools:\n")

        # Group by risk level
        safe_tools = [t for t in self.tools if t.risk_level == "safe"]
        moderate_tools = [t for t in self.tools if t.risk_level == "moderate"]
        elevated_tools = [t for t in self.tools if t.risk_level in ("elevated", "high")]
        critical_tools = [t for t in self.tools if t.risk_level == "critical"]

        if safe_tools:
            sections.append("## Safe (no confirmation required)")
            for t in safe_tools:
                sections.append(f"- `{t.slug}`: {t.description}")

        if moderate_tools:
            sections.append("\n## Moderate Risk")
            for t in moderate_tools:
                sections.append(f"- `{t.slug}`: {t.description}")

        if elevated_tools:
            sections.append("\n## Elevated Risk (confirmation recommended)")
            for t in elevated_tools:
                warn = ""
                if t.dangerous_subcommands:
                    warn = f" [DANGEROUS: {', '.join(t.dangerous_subcommands)}]"
                sections.append(f"- `{t.slug}`: {t.description}{warn}")

        if critical_tools:
            sections.append("\n## CRITICAL RISK (confirmation REQUIRED)")
            for t in critical_tools:
                sections.append(f"- `{t.slug}`: {t.description} ⚠️")

        # Communication Style
        sections.append("\n# COMMUNICATION PROTOCOL\n")
        cs = self.communication_style
        if cs.get("format"):
            sections.append(f"**Output Format:** {cs['format']}")
        if cs.get("verbosity"):
            sections.append(f"**Verbosity:** {cs['verbosity']}")
        if cs.get("tone"):
            sections.append(f"**Tone:** {cs['tone']}")

        return "\n".join(sections)

    def render_compact_prompt(self) -> str:
        """Render a compact system prompt for token-constrained contexts."""
        lines = [
            f"You are {self.agent_name}, a {self.archetype_label}.",
            f"Role: {self.role_title}",
            "",
            "CONSTRAINTS:",
        ]
        for c in self.constraints[:5]:  # Top 5 constraints
            lines.append(f"- {c}")

        lines.append("")
        lines.append(f"TOOLS: {', '.join(t.slug for t in self.tools)}")

        df = self.decision_framework
        if df.get("escalation"):
            lines.append(f"\nESCALATE IF: {df['escalation']}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Export as dictionary for serialization."""
        return {
            "agent": {
                "id": self.agent_id,
                "name": self.agent_name,
                "codename": self.agent_codename,
                "role_title": self.role_title
            },
            "archetype": {
                "slug": self.archetype_slug,
                "label": self.archetype_label
            },
            "instructions": {
                "preamble": self.preamble,
                "role": {
                    "primary": self.primary_role,
                    "secondary": self.secondary_roles
                },
                "capabilities": self.capabilities_desc,
                "constraints": self.constraints,
                "decision_framework": self.decision_framework,
                "communication_style": self.communication_style,
                "sources": self.sources
            },
            "capabilities": [
                {
                    "slug": c.slug,
                    "label": c.label,
                    "category": c.category,
                    "proficiency": c.proficiency,
                    "proficiency_desc": c.proficiency_desc
                }
                for c in self.capabilities
            ],
            "tools": [
                {
                    "slug": t.slug,
                    "label": t.label,
                    "risk_level": t.risk_level,
                    "requires_confirmation": t.requires_confirmation,
                    "dangerous_subcommands": t.dangerous_subcommands
                }
                for t in self.tools
            ],
            "team": {
                "pairs_well_with": self.pairs_well_with,
                "team_size": self.team_size
            },
            "prompts": {
                "full": self.render_system_prompt(),
                "compact": self.render_compact_prompt()
            }
        }


class AgentComposer:
    """
    Composes complete operational profiles for agents.

    Usage:
        composer = AgentComposer()

        # Compose from agent entity
        profile = composer.compose(agent)

        # Get system prompt
        prompt = profile.render_system_prompt()

        # Or compose directly from archetype for new agents
        profile = composer.compose_from_archetype("linux_admin", agent_name="ServerBot")
    """

    def __init__(self, datastore: Optional[DataStore] = None):
        self.ds = datastore or get_datastore()
        self._cache = {}
        self._load_references()

    def _load_references(self):
        """Load all reference data."""
        self._archetypes = {a["slug"]: a for a in self.ds.load_reference("archetype")}
        self._instructions = {i["slug"]: i for i in self.ds.load_reference("instruction")}
        self._capabilities = {c["id"]: c for c in self.ds.load_reference("capability")}
        self._capabilities_by_slug = {c["slug"]: c for c in self.ds.load_reference("capability")}
        self._tools = {t["slug"]: t for t in self.ds.load_reference("tool")}
        self._ranks = {r["id"]: r for r in self.ds.load_reference("rank")}
        self._qualifiers = {q["id"]: q for q in self.ds.load_reference("qualifier")}
        self._focuses = {f["id"]: f for f in self.ds.load_reference("focus")}
        self._instruction_blocks = self._load_instruction_blocks()

    def _load_instruction_blocks(self) -> list[InstructionBlock]:
        """Load composable instruction blocks from filesystem."""
        blocks = []
        instruction_dir = self.ds.base_path / "instruction"

        if not instruction_dir.exists():
            return blocks

        # Recursively find all JSON files in instruction subdirectories
        for json_file in instruction_dir.rglob("*.json"):
            try:
                with open(json_file, "r") as f:
                    data = json.load(f)
                    if "instruction_blocks" in data:
                        block = InstructionBlock.from_dict(data)
                        block.meta["source_path"] = str(json_file)
                        blocks.append(block)
            except (json.JSONDecodeError, IOError):
                continue

        # Sort by priority (lower first, so higher priority can override)
        return sorted(blocks, key=lambda b: b.priority)

    def _block_applies(self, block: InstructionBlock, context: dict) -> bool:
        """Check if an instruction block applies to the given context."""
        applies = block.applies_when
        if not applies:
            return False

        # Check archetype_ids
        if "archetype_ids" in applies:
            arch_id = context.get("archetype_id")
            if arch_id not in applies["archetype_ids"]:
                return False

        # Check focus_ids
        if "focus_ids" in applies:
            focus_id = context.get("focus_id")
            if focus_id not in applies["focus_ids"]:
                return False

        # Check rank_ids (can be list or range)
        if "rank_ids" in applies:
            rank_id = context.get("rank_id", 1)
            rank_cond = applies["rank_ids"]
            if isinstance(rank_cond, list):
                if rank_id not in rank_cond:
                    return False
            elif isinstance(rank_cond, dict):
                # Range: {"min": 1, "max": 3}
                min_rank = rank_cond.get("min", 1)
                max_rank = rank_cond.get("max", 8)
                if not (min_rank <= rank_id <= max_rank):
                    return False

        # Check scope
        if "scope" in applies:
            if context.get("scope") != applies["scope"]:
                return False

        return True

    def _merge_instruction_blocks(
        self,
        base_instruction: dict,
        applicable_blocks: list[InstructionBlock]
    ) -> dict:
        """
        Merge instruction blocks into a base instruction.

        Blocks are applied in priority order (low to high).
        Higher priority blocks can override or extend lower priority content.
        """
        result = {
            "preamble": base_instruction.get("preamble", ""),
            "role": dict(base_instruction.get("role", {})),
            "capabilities": list(base_instruction.get("capabilities", [])),
            "constraints": list(base_instruction.get("constraints", [])),
            "decision_framework": dict(base_instruction.get("decision_framework", {})),
            "communication_style": dict(base_instruction.get("communication_style", {})),
            "tool_risk_overrides": dict(base_instruction.get("tool_risk_overrides", {})),
            "tool_locks": [],
            "tool_unlocks": []
        }

        for block in applicable_blocks:
            b = block.blocks

            # Append to preamble
            if b.get("preamble_append"):
                result["preamble"] += b["preamble_append"]

            # Add role additions
            if b.get("role_additions"):
                secondary = result["role"].get("secondary", [])
                secondary.extend(b["role_additions"])
                result["role"]["secondary"] = secondary

            # Add capability additions
            if b.get("capability_additions"):
                result["capabilities"].extend(b["capability_additions"])

            # Add capability restrictions (stored separately for rendering)
            if b.get("capability_restrictions"):
                result.setdefault("capability_restrictions", [])
                result["capability_restrictions"].extend(b["capability_restrictions"])

            # Add constraints
            if b.get("constraint_additions"):
                result["constraints"].extend(b["constraint_additions"])

            # Remove constraints (by exact match or substring)
            if b.get("constraint_removals"):
                removals = set(b["constraint_removals"])
                result["constraints"] = [
                    c for c in result["constraints"]
                    if not any(r in c or r == c for r in removals)
                ]

            # Expertise context (added to capabilities)
            if b.get("expertise_context"):
                result["capabilities"].extend(b["expertise_context"])

            # Tool unlocks and locks
            if b.get("tool_unlocks"):
                result["tool_unlocks"].extend(b["tool_unlocks"])
            if b.get("tool_locks"):
                result["tool_locks"].extend(b["tool_locks"])

            # Tool risk adjustments (override by tool slug)
            if b.get("tool_risk_adjustments"):
                for tool, adjustment in b["tool_risk_adjustments"].items():
                    result["tool_risk_overrides"][tool] = adjustment

            # Decision framework overrides
            if b.get("decision_framework_overrides"):
                result["decision_framework"].update(b["decision_framework_overrides"])

            # Communication style overrides
            if b.get("communication_style_overrides"):
                result["communication_style"].update(b["communication_style_overrides"])

        return result

    def compose(
        self,
        agent: dict,
        archetype_slug: Optional[str] = None,
        scope: str = "supervised"
    ) -> OperationalProfile:
        """
        Compose a complete operational profile for an agent.

        Args:
            agent: Agent entity dict
            archetype_slug: Override archetype (default: infer from agent focus/rank)
            scope: Operational scope (read_only, supervised, autonomous)

        Returns:
            Complete OperationalProfile ready for dispatch
        """
        # Determine archetype
        if archetype_slug is None:
            archetype_slug = self._infer_archetype(agent)

        archetype = self._archetypes.get(archetype_slug)
        if not archetype:
            raise ValueError(f"Unknown archetype: {archetype_slug}")

        # Get base instruction template
        instruction_slug = archetype.get("instruction_template", archetype_slug)
        base_instruction = dict(self._instructions.get(instruction_slug, {}))

        # Build context for instruction block matching
        cls = agent.get("classification", {})
        context = {
            "archetype_id": archetype.get("id"),
            "archetype_slug": archetype_slug,
            "rank_id": cls.get("rank_id", 1),
            "focus_id": cls.get("focus_id"),
            "scope": scope
        }

        # Find and apply applicable instruction blocks
        applicable_blocks = [
            block for block in self._instruction_blocks
            if self._block_applies(block, context)
        ]

        # Merge instruction blocks into base instruction
        merged = self._merge_instruction_blocks(base_instruction, applicable_blocks)

        # Build capability list
        capabilities = self._build_capabilities(archetype, agent)

        # Build tool access list (with merged tool risk overrides)
        tools = self._build_tool_access(archetype, agent, merged)

        # Apply tool locks from merged blocks
        if merged.get("tool_locks"):
            locked = set(merged["tool_locks"])
            tools = [t for t in tools if t.slug not in locked]

        # Extract agent identity
        ident = agent.get("identity", {})
        role_title = ident.get("role_title", "Operative")

        return OperationalProfile(
            agent_id=agent.get("id", "unknown"),
            agent_name=ident.get("name", "Unknown Agent"),
            agent_codename=ident.get("codename", "unknown.agent"),
            role_title=role_title,
            archetype_slug=archetype_slug,
            archetype_label=archetype.get("label", archetype_slug),
            preamble=merged.get("preamble", f"You are a {archetype.get('label', 'specialist')}."),
            primary_role=merged.get("role", {}).get("primary", "Perform assigned tasks."),
            secondary_roles=merged.get("role", {}).get("secondary", []),
            capabilities_desc=merged.get("capabilities", []),
            constraints=merged.get("constraints", []),
            decision_framework=merged.get("decision_framework", {}),
            communication_style=merged.get("communication_style", {}),
            capabilities=capabilities,
            tools=tools,
            tool_risk_overrides=merged.get("tool_risk_overrides", {}),
            pairs_well_with=archetype.get("pairs_well_with", []),
            team_size=archetype.get("team_size", {"min": 1, "max": 1}),
            sources=(
                [f"reference:instruction/{instruction_slug}"]
                + [b.meta.get("source_path", b.slug) for b in applicable_blocks]
            )
        )

    def compose_from_archetype(
        self,
        archetype_slug: str,
        agent_name: str = "Agent",
        agent_codename: str = "agent.one",
        role_title: Optional[str] = None,
        rank_id: int = 4,
        scope: str = "supervised"
    ) -> OperationalProfile:
        """
        Compose a profile directly from an archetype without an existing agent.
        Useful for creating new agent templates or testing.

        Args:
            archetype_slug: The archetype to compose from
            agent_name: Display name for the agent
            agent_codename: Unique codename
            role_title: Role title (defaults to archetype label)
            rank_id: Rank level 1-8 (affects instruction block selection)
            scope: Operational scope (read_only, supervised, autonomous)
        """
        archetype = self._archetypes.get(archetype_slug)
        if not archetype:
            raise ValueError(f"Unknown archetype: {archetype_slug}")

        # Create a minimal agent dict
        agent = {
            "id": "template",
            "identity": {
                "name": agent_name,
                "codename": agent_codename,
                "role_title": role_title or f"{archetype.get('label', 'Specialist')}"
            },
            "classification": {
                "rank_id": rank_id,
                "qualifier_id": 3,  # Senior
            },
            "economy": {"xp": 1000}
        }

        return self.compose(agent, archetype_slug, scope=scope)

    def list_instruction_blocks(self, category: Optional[str] = None) -> list[dict]:
        """List available instruction blocks, optionally filtered by category."""
        blocks = self._instruction_blocks
        if category:
            blocks = [b for b in blocks if b.category == category]

        return [
            {
                "id": b.id,
                "slug": b.slug,
                "label": b.label,
                "category": b.category,
                "priority": b.priority,
                "description": b.meta.get("description", "")
            }
            for b in blocks
        ]

    def _infer_archetype(self, agent: dict) -> str:
        """Infer the best archetype based on agent's focus and skills."""
        cls = agent.get("classification", {})
        focus_id = cls.get("focus_id")

        if focus_id:
            focus = self._focuses.get(focus_id, {})
            focus_slug = focus.get("slug", "")

            # Map focus areas to archetypes
            focus_archetype_map = {
                # Facility
                "datacenter": "linux_admin",
                "colo": "linux_admin",
                "power": "linux_admin",
                "cooling": "linux_admin",
                "cabling": "network_engineer",
                # Network
                "core": "network_engineer",
                "edge": "network_engineer",
                "firewall": "security_analyst",
                "loadbalancer": "network_engineer",
                "wireless": "network_engineer",
                "vpn": "network_engineer",
                "dns": "network_engineer",
                # Platform
                "server": "linux_admin",
                "vm": "linux_admin",
                "container": "devops_engineer",
                "storage": "linux_admin",
                "cloud": "cloud_engineer",
                "kubernetes": "platform_engineer",
                "os": "linux_admin",
                # Application
                "service": "backend_dev",
                "api": "backend_dev",
                "middleware": "backend_dev",
                "pipeline": "devops_engineer",
                "interface": "backend_dev",
                "protocol": "network_engineer",
                "config": "devops_engineer",
                # Data
                "database": "dba",
                "warehouse": "data_engineer",
                "lake": "data_engineer",
                "etl": "data_engineer",
                "analytics": "data_engineer",
                "governance": "data_engineer",
                "ml": "data_engineer",
            }

            if focus_slug in focus_archetype_map:
                return focus_archetype_map[focus_slug]

        # Default to linux_admin
        return "linux_admin"

    def _build_capabilities(self, archetype: dict, agent: dict) -> list[CapabilityLevel]:
        """Build capability list with proficiency levels."""
        capabilities = []

        for cap_req in archetype.get("core_capabilities", []):
            cap_id = cap_req.get("capability_id")
            min_prof = cap_req.get("min_proficiency", 1)

            cap = self._capabilities.get(cap_id)
            if not cap:
                continue

            # Determine agent's proficiency (could be based on XP, skills, etc.)
            # For now, use min_proficiency from archetype + bonus from rank
            rank_id = agent.get("classification", {}).get("rank_id", 1)
            rank_bonus = min(2, (rank_id - 1) // 2)  # +1 proficiency per 2 ranks
            proficiency = min(5, min_prof + rank_bonus)

            prof_levels = cap.get("proficiency_levels", {})
            prof_desc = prof_levels.get(str(proficiency), "Competent")

            capabilities.append(CapabilityLevel(
                slug=cap["slug"],
                label=cap["label"],
                category=cap.get("category", "general"),
                proficiency=proficiency,
                proficiency_desc=prof_desc,
                unlocked_tools=cap.get("unlocks_tools", [])
            ))

        return capabilities

    def _build_tool_access(
        self,
        archetype: dict,
        agent: dict,
        instruction: dict
    ) -> list[ToolAccess]:
        """Build tool access list with risk metadata."""
        tools = []
        tool_overrides = instruction.get("tool_risk_overrides", {})

        for tool_slug in archetype.get("tool_access", []):
            tool = self._tools.get(tool_slug)
            if not tool:
                continue

            risk = tool.get("risk", {})
            override = tool_overrides.get(tool_slug, {})

            # Apply overrides
            requires_confirm = override.get(
                "requires_confirmation",
                risk.get("requires_confirmation", False)
            )

            tools.append(ToolAccess(
                slug=tool_slug,
                label=tool.get("label", tool_slug),
                description=tool.get("description", ""),
                risk_level=risk.get("level", "moderate"),
                requires_confirmation=requires_confirm,
                reversibility=risk.get("reversibility", "varies"),
                scope=risk.get("scope", "local"),
                dangerous_subcommands=risk.get("dangerous_subcommands", []),
                whispers=tool.get("usage", {}).get("whispers", [])
            ))

        return tools

    def list_archetypes(self) -> list[dict]:
        """List available archetypes with summary info."""
        return [
            {
                "slug": a["slug"],
                "label": a["label"],
                "icon": a.get("icon", ""),
                "description": a.get("description", ""),
                "tool_count": len(a.get("tool_access", [])),
                "capability_count": len(a.get("core_capabilities", []))
            }
            for a in self._archetypes.values()
        ]


# Module-level convenience
_default_composer: Optional[AgentComposer] = None


def get_composer() -> AgentComposer:
    """Get the default composer instance."""
    global _default_composer
    if _default_composer is None:
        _default_composer = AgentComposer()
    return _default_composer


def compose_agent(
    agent: dict,
    archetype_slug: Optional[str] = None,
    scope: str = "supervised"
) -> OperationalProfile:
    """Convenience function to compose an agent profile."""
    return get_composer().compose(agent, archetype_slug, scope=scope)


def compose_from_archetype(archetype_slug: str, **kwargs) -> OperationalProfile:
    """Convenience function to compose from archetype."""
    return get_composer().compose_from_archetype(archetype_slug, **kwargs)


def list_instruction_blocks(category: Optional[str] = None) -> list[dict]:
    """Convenience function to list instruction blocks."""
    return get_composer().list_instruction_blocks(category)
