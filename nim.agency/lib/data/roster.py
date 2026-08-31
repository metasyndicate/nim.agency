"""
Roster - competency-based agent selection.

Resolves operator-supplied competency terms ("regex", "dns", "security")
against an agent's full competency surface - skill domains, tools,
specialties, and capabilities - and produces scored, explainable matches
for crew selection. When the roster can't fill a request, a general-purpose
hire can be drafted on the spot.
"""

from dataclasses import dataclass, field
from typing import Optional

from .crud import DataStore, get_datastore
from .generator import AgentGenerator


# Score weights. A direct tool hit is the strongest per-point signal (the
# operator asked for a competency the agent verifiably carries); skill
# domains scale with invested points; a specialty hit is a persona-level
# match. Conduct violations cost standing - repeat offenders get fewer gigs.
WEIGHT_TOOL = 3
WEIGHT_SKILL = 2
WEIGHT_CAPABILITY = 2
SPECIALTY_BONUS = 15
VIOLATION_PENALTY = 5


@dataclass
class RosterMatch:
    """A scored candidate with the reasons it matched."""
    agent: dict
    score: int = 0
    reasons: list[str] = field(default_factory=list)  # "regex->tool:grep(p3)"

    @property
    def codename(self) -> str:
        return self.agent["identity"]["codename"]


def _text_surfaces(entry: dict) -> list[str]:
    """Searchable text for a reference entry: slug, label, description,
    and any usage whispers (tools only)."""
    surfaces = [
        entry.get("slug", ""),
        entry.get("label", ""),
        entry.get("description", ""),
    ]
    surfaces.extend((entry.get("usage") or {}).get("whispers", []))
    return [s.lower() for s in surfaces if s]


def _match_reference(term: str, entries: list[dict]) -> dict[int, dict]:
    """Reference entries whose text surfaces contain the term, keyed by id."""
    t = term.lower().strip()
    hits = {}
    for entry in entries:
        for surface in _text_surfaces(entry):
            if t in surface:
                hits[entry["id"]] = entry
                break
    return hits


def match_agents(
    terms: list[str],
    count: int = 1,
    ds: Optional[DataStore] = None
) -> list[RosterMatch]:
    """
    Rank roster agents against competency terms.

    Each term is matched against the slug/label/description (and tool
    whispers) of skill domains, tools, specialties, and capabilities;
    agents accumulate score from the competencies they actually hold.
    Returns the top `count` positive-score candidates, best first.
    """
    ds = ds or get_datastore()

    skill_domains = ds.load_reference("skill_domain")
    tools = ds.load_reference("tool")
    specialties = ds.load_reference("specialty")
    capabilities = ds.load_reference("capability")

    # Resolve terms to reference ids once, independent of the roster
    term_hits = []
    for term in terms:
        term_hits.append({
            "term": term,
            "skills": _match_reference(term, skill_domains),
            "tools": _match_reference(term, tools),
            "specialties": _match_reference(term, specialties),
            "capabilities": _match_reference(term, capabilities),
        })

    matches = []
    for agent in ds.query("agent").data:
        match = RosterMatch(agent=agent)

        for hits in term_hits:
            term = hits["term"]

            for skill in agent.get("skills", []):
                domain = hits["skills"].get(skill.get("domain_id"))
                if domain and skill.get("points", 0) > 0:
                    match.score += skill["points"] * WEIGHT_SKILL
                    match.reasons.append(
                        f"{term}->skill:{domain['slug']}({skill['points']}pts)")

            for tool in agent.get("tools", []):
                entry = hits["tools"].get(tool.get("tool_id"))
                if entry:
                    prof = tool.get("proficiency", 1)
                    match.score += prof * WEIGHT_TOOL
                    match.reasons.append(f"{term}->tool:{entry['slug']}(p{prof})")

            specialty = hits["specialties"].get(
                agent.get("classification", {}).get("specialty_id"))
            if specialty:
                match.score += SPECIALTY_BONUS
                match.reasons.append(f"{term}->specialty:{specialty['slug']}")

            for cap in agent.get("capabilities", []):
                entry = hits["capabilities"].get(cap.get("capability_id"))
                if entry:
                    prof = cap.get("proficiency", 1)
                    match.score += prof * WEIGHT_CAPABILITY
                    match.reasons.append(
                        f"{term}->capability:{entry['slug']}(p{prof})")

        violations = (agent.get("conduct") or {}).get("violations", 0)
        if match.score > 0 and violations:
            match.score -= violations * VIOLATION_PENALTY
            match.reasons.append(f"conduct:-{violations * VIOLATION_PENALTY}")

        if match.score > 0:
            matches.append(match)

    matches.sort(
        key=lambda m: (m.score, m.agent.get("economy", {}).get("xp", 0)),
        reverse=True,
    )
    return matches[:count]


def draft_hire(ds: Optional[DataStore] = None) -> dict:
    """
    Generate and enroll a general-purpose agent on the spot.

    Fallback for when the roster can't fill a dispatch request (empty, or
    no competency match). The hire is a full roster member - origin marks
    it as drafted by dispatch rather than deliberately generated.
    """
    ds = ds or get_datastore()
    agent = AgentGenerator(ds).generate()
    agent["origin"]["client"] = "dispatch"
    agent["origin"]["discovery"] = "drafted"
    agent = ds.create("agent", agent, validate=False)

    from .identity import try_enroll
    enrollment = try_enroll(ds, agent)
    return enrollment.agent if enrollment else agent
