"""
OWL - Operative Wrangler Layer

The crüe composer. Assembles agent teams, manages hierarchies,
and orchestrates multi-agent operations.

"Who watches the operatives? The OWL watches."
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone
import uuid

from .crud import DataStore, get_datastore
from .composer import AgentComposer, OperationalProfile, get_composer


@dataclass
class AgentSlot:
    """A slot in a crüe roster."""
    archetype: str
    role: str  # lead, primary, support
    agent_id: Optional[str] = None
    agent: Optional[dict] = None
    profile: Optional[OperationalProfile] = None
    relation_to_lead: Optional[str] = None  # subordinate, peer


@dataclass
class CrüeRoster:
    """Assembled crüe with agents and operational context."""
    id: str
    crue_type_slug: str
    crue_type_label: str
    mission_profile: str

    # Composition
    lead: AgentSlot
    members: list[AgentSlot]

    # Coordination
    coordination_style: str
    handoff_protocol: str
    escalation_path: str

    # Metadata
    assembled_at: str
    icon: str

    @property
    def size(self) -> int:
        return 1 + len(self.members)  # lead + members

    @property
    def all_agents(self) -> list[AgentSlot]:
        return [self.lead] + self.members

    def render_briefing(self) -> str:
        """Render a mission briefing for the crüe."""
        lines = [
            f"# CRÜE BRIEFING: {self.crue_type_label.upper()}",
            f"**Mission Profile:** {self.mission_profile}",
            f"**Crüe Size:** {self.size} operatives",
            f"**Coordination:** {self.coordination_style}",
            "",
            "## ROSTER",
            ""
        ]

        # Lead
        lines.append(f"### LEAD: {self.lead.agent['identity']['name']}")
        lines.append(f"- Archetype: {self.lead.archetype}")
        lines.append(f"- Role Title: {self.lead.agent['identity'].get('role_title', 'N/A')}")
        lines.append(f"- Codename: {self.lead.agent['identity']['codename']}")
        lines.append("")

        # Members
        for i, member in enumerate(self.members, 1):
            lines.append(f"### MEMBER {i}: {member.agent['identity']['name']}")
            lines.append(f"- Archetype: {member.archetype}")
            lines.append(f"- Role: {member.role}")
            lines.append(f"- Relation to Lead: {member.relation_to_lead}")
            lines.append("")

        # Coordination Protocol
        lines.append("## COORDINATION PROTOCOL")
        lines.append(f"- **Style:** {self.coordination_style}")
        lines.append(f"- **Handoff:** {self.handoff_protocol}")
        lines.append(f"- **Escalation:** {self.escalation_path}")

        return "\n".join(lines)

    def render_dispatch_payload(self) -> dict:
        """Render the full dispatch payload with all agent profiles."""
        return {
            "crue": {
                "id": self.id,
                "type": self.crue_type_slug,
                "label": self.crue_type_label,
                "mission": self.mission_profile,
                "icon": self.icon
            },
            "coordination": {
                "style": self.coordination_style,
                "handoff": self.handoff_protocol,
                "escalation": self.escalation_path
            },
            "roster": {
                "lead": {
                    "agent_id": self.lead.agent_id,
                    "archetype": self.lead.archetype,
                    "profile": self.lead.profile.to_dict() if self.lead.profile else None
                },
                "members": [
                    {
                        "agent_id": m.agent_id,
                        "archetype": m.archetype,
                        "role": m.role,
                        "relation": m.relation_to_lead,
                        "profile": m.profile.to_dict() if m.profile else None
                    }
                    for m in self.members
                ]
            },
            "assembled_at": self.assembled_at
        }


class OWL:
    """
    Operative Wrangler Layer - The crüe composer.

    Responsibilities:
    - Assemble crües from available agents
    - Match agents to archetype requirements
    - Establish agent hierarchies and relationships
    - Generate coordinated mission profiles

    Usage:
        owl = OWL()

        # Assemble a crüe from available agents
        roster = owl.assemble("road_crue")

        # Or assemble with specific agents
        roster = owl.assemble("frogmen", agents=[agent1, agent2])

        # Get briefing
        print(roster.render_briefing())

        # Get dispatch payload
        payload = roster.render_dispatch_payload()
    """

    def __init__(
        self,
        datastore: Optional[DataStore] = None,
        composer: Optional[AgentComposer] = None
    ):
        self.ds = datastore or get_datastore()
        self.composer = composer or get_composer()
        self._load_references()

    def _load_references(self):
        """Load reference data."""
        self._crue_types = {c["slug"]: c for c in self.ds.load_reference("crue_type")}
        self._archetypes = {a["slug"]: a for a in self.ds.load_reference("archetype")}
        self._relations = {r["slug"]: r for r in self.ds.load_reference("agent_relation")}
        self._focuses = {f["id"]: f for f in self.ds.load_reference("focus")}

    def list_crue_types(self) -> list[dict]:
        """List available crüe types."""
        return [
            {
                "slug": c["slug"],
                "label": c["label"],
                "icon": c.get("icon", ""),
                "description": c.get("description", ""),
                "mission_profile": c.get("mission_profile", ""),
                "size_range": f"{c['composition']['min_size']}-{c['composition']['max_size']}"
            }
            for c in self._crue_types.values()
        ]

    def assemble(
        self,
        crue_type_slug: str,
        agents: Optional[list[dict]] = None,
        auto_fill: bool = True
    ) -> CrüeRoster:
        """
        Assemble a crüe of the specified type.

        Args:
            crue_type_slug: Type of crüe to assemble
            agents: Optional list of agents to use (will match to archetypes)
            auto_fill: If True, fill missing slots from available agents

        Returns:
            Assembled CrüeRoster ready for dispatch
        """
        crue_type = self._crue_types.get(crue_type_slug)
        if not crue_type:
            raise ValueError(f"Unknown crüe type: {crue_type_slug}")

        composition = crue_type["composition"]
        primary_archetypes = crue_type.get("primary_archetypes", [])
        support_archetypes = crue_type.get("support_archetypes", [])
        lead_archetype = composition.get("lead_archetype", primary_archetypes[0] if primary_archetypes else None)

        # Get available agents if not provided
        if agents is None and auto_fill:
            agents = self.ds.query("agent", filter_fn=lambda a:
                a.get("classification", {}).get("status_id") == 2  # Operational
            ).data

        agents = agents or []

        # Score and match agents to archetypes
        agent_scores = self._score_agents(agents, primary_archetypes + support_archetypes)

        # Select lead
        lead_agent, lead_profile = self._select_for_archetype(
            agent_scores, lead_archetype, exclude=[]
        )

        lead_slot = AgentSlot(
            archetype=lead_archetype,
            role="lead",
            agent_id=lead_agent["id"] if lead_agent else None,
            agent=lead_agent,
            profile=lead_profile,
            relation_to_lead=None
        )

        # Fill remaining slots
        members = []
        used_agents = [lead_agent["id"]] if lead_agent else []
        min_members = composition["min_size"] - 1  # -1 for lead

        # First, fill primary archetype slots
        for archetype in primary_archetypes:
            if archetype == lead_archetype:
                continue  # Already have lead
            if len(members) >= composition["max_size"] - 1:
                break

            agent, profile = self._select_for_archetype(
                agent_scores, archetype, exclude=used_agents
            )
            if agent:
                members.append(AgentSlot(
                    archetype=archetype,
                    role="primary",
                    agent_id=agent["id"],
                    agent=agent,
                    profile=profile,
                    relation_to_lead="peer"
                ))
                used_agents.append(agent["id"])

        # Fill support slots if needed
        if len(members) < min_members:
            for archetype in support_archetypes:
                if len(members) >= min_members:
                    break

                agent, profile = self._select_for_archetype(
                    agent_scores, archetype, exclude=used_agents
                )
                if agent:
                    members.append(AgentSlot(
                        archetype=archetype,
                        role="support",
                        agent_id=agent["id"],
                        agent=agent,
                        profile=profile,
                        relation_to_lead="subordinate"
                    ))
                    used_agents.append(agent["id"])

        coordination = crue_type.get("coordination", {})

        return CrüeRoster(
            id=str(uuid.uuid4()),
            crue_type_slug=crue_type_slug,
            crue_type_label=crue_type["label"],
            mission_profile=crue_type.get("mission_profile", ""),
            lead=lead_slot,
            members=members,
            coordination_style=coordination.get("style", "collaborative"),
            handoff_protocol=coordination.get("handoff", "explicit"),
            escalation_path=coordination.get("escalation", "lead"),
            assembled_at=datetime.now(timezone.utc).isoformat(),
            icon=crue_type.get("icon", "👥")
        )

    def _score_agents(
        self,
        agents: list[dict],
        target_archetypes: list[str]
    ) -> dict[str, list[tuple[dict, float, str]]]:
        """
        Score agents against target archetypes.

        Returns:
            Dict mapping archetype -> [(agent, score, inferred_archetype), ...]
        """
        scores = {arch: [] for arch in target_archetypes}

        for agent in agents:
            inferred = self._infer_archetype(agent)

            for target_arch in target_archetypes:
                score = self._calculate_fit_score(agent, target_arch, inferred)
                scores[target_arch].append((agent, score, inferred))

        # Sort each archetype's candidates by score
        for arch in scores:
            scores[arch].sort(key=lambda x: x[1], reverse=True)

        return scores

    def _calculate_fit_score(
        self,
        agent: dict,
        target_archetype: str,
        inferred_archetype: str
    ) -> float:
        """Calculate how well an agent fits a target archetype."""
        score = 0.0

        # Perfect match bonus
        if inferred_archetype == target_archetype:
            score += 50.0

        # Check if archetypes pair well
        archetype = self._archetypes.get(target_archetype, {})
        pairs_well = archetype.get("pairs_well_with", [])
        if inferred_archetype in pairs_well:
            score += 20.0

        # Rank bonus (higher rank = more capable)
        rank_id = agent.get("classification", {}).get("rank_id", 1)
        score += rank_id * 3.0

        # XP bonus
        xp = agent.get("economy", {}).get("xp", 0)
        score += min(20.0, xp / 500)

        # Health penalty (don't pick injured agents)
        health = agent.get("economy", {}).get("health", {})
        current_health = health.get("current", 100)
        max_health = health.get("max", 100)
        if max_health > 0:
            health_ratio = current_health / max_health
            if health_ratio < 0.5:
                score -= 30.0
            elif health_ratio < 0.8:
                score -= 10.0

        return score

    def _select_for_archetype(
        self,
        scores: dict,
        archetype: str,
        exclude: list[str]
    ) -> tuple[Optional[dict], Optional[OperationalProfile]]:
        """Select best available agent for an archetype."""
        candidates = scores.get(archetype, [])

        for agent, score, inferred in candidates:
            if agent["id"] not in exclude:
                profile = self.composer.compose(agent, archetype)
                return agent, profile

        return None, None

    def _infer_archetype(self, agent: dict) -> str:
        """Infer archetype from agent's focus."""
        # Reuse composer's inference logic
        return self.composer._infer_archetype(agent)

    def recommend_crue(self, mission_keywords: list[str]) -> list[str]:
        """
        Recommend crüe types based on mission keywords.

        Args:
            mission_keywords: Keywords describing the mission

        Returns:
            List of recommended crüe type slugs, ranked by fit
        """
        keyword_map = {
            # Network keywords
            "network": ["road_crue", "pae"],
            "dns": ["road_crue"],
            "firewall": ["pae", "road_crue"],
            "routing": ["road_crue"],
            "connectivity": ["road_crue", "frogmen"],

            # Platform keywords
            "server": ["ground_crue"],
            "vm": ["ground_crue"],
            "container": ["ground_crue", "weld_crue"],
            "kubernetes": ["ground_crue", "paratroopers"],
            "deploy": ["paratroopers", "ground_crue"],

            # Data keywords
            "database": ["data_crue", "frogmen"],
            "etl": ["data_crue"],
            "pipeline": ["data_crue", "weld_crue"],
            "migration": ["data_crue", "wrecking_crue"],

            # Incident keywords
            "incident": ["paratroopers", "sru"],
            "outage": ["sru", "paratroopers"],
            "emergency": ["sru", "paratroopers"],
            "recovery": ["sru"],
            "disaster": ["sru"],

            # Security keywords
            "security": ["pae", "frogmen"],
            "access": ["pae"],
            "identity": ["pae"],
            "breach": ["pae", "paratroopers"],

            # Integration keywords
            "api": ["weld_crue"],
            "integration": ["weld_crue"],
            "middleware": ["weld_crue"],

            # Cleanup keywords
            "cleanup": ["wrecking_crue"],
            "decommission": ["wrecking_crue"],
            "legacy": ["wrecking_crue", "frogmen"],
            "technical debt": ["wrecking_crue"],

            # Scale keywords
            "mass": ["horde"],
            "fleet": ["horde"],
            "bulk": ["horde"],
            "parallel": ["horde"],

            # Debug keywords
            "debug": ["frogmen"],
            "troubleshoot": ["frogmen"],
            "investigate": ["frogmen"],
            "root cause": ["frogmen"],
        }

        scores = {}
        for keyword in mission_keywords:
            keyword_lower = keyword.lower()
            for kw, crues in keyword_map.items():
                if kw in keyword_lower or keyword_lower in kw:
                    for i, crue in enumerate(crues):
                        # Higher score for first recommendations
                        scores[crue] = scores.get(crue, 0) + (10 - i)

        # Sort by score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [crue for crue, score in ranked]


# Module-level convenience
_default_owl: Optional[OWL] = None


def get_owl() -> OWL:
    """Get the default OWL instance."""
    global _default_owl
    if _default_owl is None:
        _default_owl = OWL()
    return _default_owl


def assemble_crue(crue_type: str, **kwargs) -> CrüeRoster:
    """Convenience function to assemble a crüe."""
    return get_owl().assemble(crue_type, **kwargs)
