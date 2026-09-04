"""
Economy - the paymaster: XP, gold, rank progression, and the gold ledger.

Wires mission outcomes into the RPG economy. Completed tasks earn XP and
gold, full mission success pays a bonus, and every gold movement lands in
the immutable transaction ledger. XP drives qualifier bands and rank
promotion; promotions grant unassigned skill points to spend at the dojo
(`train`), while gold buys tooling at the depot (`equip`).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..config import get_config
from .crud import DataStore, get_datastore


# Fallbacks when etc/agency.json predates the economy section
ECONOMY_DEFAULTS = {
    "xp_per_task": 25,
    "gold_per_task": 10,
    "mission_bonus_xp": 15,
    "mission_bonus_gold": 5,
    "skill_points_per_rank": 10,
    "max_tool_proficiency": 3,
}


def economy_config() -> dict:
    cfg = dict(ECONOMY_DEFAULTS)
    cfg.update(get_config().get("economy", {}))
    return cfg


@dataclass
class Payout:
    """One agent's settlement from a single award or spend."""
    agent: dict
    xp: int = 0
    gold: int = 0
    tasks_completed: int = 0
    skill_points_awarded: int = 0
    rank_change: Optional[tuple[str, str]] = None       # (old label, new label)
    qualifier_change: Optional[tuple[str, str]] = None  # (old label, new label)
    notes: list[str] = field(default_factory=list)

    @property
    def codename(self) -> str:
        return self.agent["identity"]["codename"]

    def summary_line(self) -> str:
        parts = []
        if self.xp:
            parts.append(f"{self.xp:+d}xp")
        if self.gold:
            parts.append(f"{self.gold:+d}g")
        if self.skill_points_awarded:
            parts.append(f"+{self.skill_points_awarded}sp")
        if self.rank_change:
            parts.append(f"RANK UP: {self.rank_change[0]} -> {self.rank_change[1]}")
        if self.qualifier_change:
            parts.append(f"now {self.qualifier_change[1]}")
        parts.extend(self.notes)
        return f"{self.codename}  {'  '.join(parts) if parts else 'no change'}"


def _ref_by_id(ds: DataStore, entity_type: str) -> dict[int, dict]:
    return {e["id"]: e for e in ds.load_reference(entity_type)}


def _qualifier_for_xp(ds: DataStore, xp: int) -> Optional[dict]:
    for q in ds.load_reference("qualifier"):
        max_xp = q.get("max_xp")
        if q.get("min_xp", 0) <= xp and (max_xp is None or xp <= max_xp):
            return q
    return None


def _rank_for_xp(ds: DataStore, xp: int, agent: dict) -> dict:
    """Highest rank the XP unlocks. Where thresholds tie (the tier-4
    Synthesizer/Theorist fork), keep the agent's current rank if eligible,
    otherwise pick deterministically from the agent id so re-runs agree."""
    ranks = ds.load_reference("rank")
    eligible = [r for r in ranks if r.get("xp_threshold", 0) <= xp]
    if not eligible:
        return min(ranks, key=lambda r: r.get("xp_threshold", 0))

    top = max(r.get("xp_threshold", 0) for r in eligible)
    candidates = sorted(
        (r for r in eligible if r.get("xp_threshold", 0) == top),
        key=lambda r: r["id"],
    )
    current_id = agent.get("classification", {}).get("rank_id")
    for r in candidates:
        if r["id"] == current_id:
            return r
    seed = int(str(agent.get("id", "0")).replace("-", "") or "0", 16)
    return candidates[seed % len(candidates)]


def _next_transaction_id(ds: DataStore) -> int:
    entries = ds.query("transaction", include_retired=True).data
    return max((e.get("id", 0) for e in entries if isinstance(e.get("id"), int)),
               default=0) + 1


def _record_transaction(
    ds: DataStore,
    agent: dict,
    amount: int,
    tx_type: str,
    reference: Optional[dict] = None,
    notes: str = "",
    outbound: bool = False
) -> None:
    """Append a gold movement to the immutable ledger. Amounts are always
    positive; direction is carried by from/to (outbound = agent spends)."""
    if amount <= 0:
        return
    ds.create("transaction", {
        "id": _next_transaction_id(ds),
        "from_agent_id": agent["id"] if outbound else None,
        "to_agent_id": None if outbound else agent["id"],
        "amount": amount,
        "type": tx_type,
        "reference": reference or {"type": "manual", "id": ""},
        "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, validate=False)


def apply_award(
    ds: DataStore,
    agent: dict,
    xp: int = 0,
    gold: int = 0,
    tx_type: str = "bonus",
    reference: Optional[dict] = None,
    note: str = ""
) -> Payout:
    """
    Settle an XP/gold award (or penalty) against an agent: adjust balances,
    recompute qualifier and rank, grant promotion skill points, persist, and
    ledger the gold movement.
    """
    cfg = economy_config()
    economy = agent.get("economy", {})
    payout = Payout(agent=agent, xp=xp, gold=gold)

    old_xp = economy.get("xp", 0)
    new_xp = max(0, old_xp + xp)

    old_gold = economy.get("gold", 0)
    new_gold = old_gold + gold
    if new_gold < 0:
        payout.notes.append(f"gold floored at 0 (was {old_gold})")
        payout.gold = -old_gold
        new_gold = 0

    # Qualifier band follows XP directly
    old_q = _qualifier_for_xp(ds, old_xp)
    new_q = _qualifier_for_xp(ds, new_xp)
    if old_q and new_q and old_q["id"] != new_q["id"]:
        payout.qualifier_change = (old_q["label"], new_q["label"])

    # Rank promotion; one skill-point grant per threshold crossed
    ranks = _ref_by_id(ds, "rank")
    old_rank = ranks.get(agent.get("classification", {}).get("rank_id"))
    new_rank = _rank_for_xp(ds, new_xp, agent)
    updates = {
        "economy": {"xp": new_xp, "gold": new_gold},
    }
    old_thr = old_rank.get("xp_threshold", 0) if old_rank else 0
    new_thr = new_rank.get("xp_threshold", 0)
    if new_thr > old_thr:
        payout.rank_change = (
            old_rank["label"] if old_rank else "?", new_rank["label"])
        thresholds_crossed = len({
            r.get("xp_threshold", 0) for r in ranks.values()
            if old_thr < r.get("xp_threshold", 0) <= new_thr
        })
        payout.skill_points_awarded = (
            thresholds_crossed * cfg["skill_points_per_rank"])
        updates["classification"] = {"rank_id": new_rank["id"]}
        updates["economy"]["skill_points"] = {
            "total": economy.get("skill_points", {}).get("total", 0)
                     + payout.skill_points_awarded,
        }
    if new_q:
        updates.setdefault("classification", {})["qualifier_id"] = new_q["id"]

    updated = ds.update("agent", agent["id"], updates)
    payout.agent = updated or agent

    if gold > 0:
        _record_transaction(ds, agent, gold, tx_type, reference, note)
    elif gold < 0:
        # payout.gold holds the deduction actually applied post-floor
        _record_transaction(ds, agent, -payout.gold, "penalty", reference,
                            note, outbound=True)
    return payout


def award_mission(ds: DataStore, mission) -> list[Payout]:
    """
    Post-mission payroll: pay each crew member per completed task, plus a
    bonus (and a missions_completed credit) when the whole mission lands.
    Agents with no completed tasks get nothing - the conduct ledger already
    handled any misbehavior.
    """
    cfg = economy_config()
    mission_complete = getattr(mission.status, "value", mission.status) == "complete"
    reference = {"type": "mission", "id": mission.id}

    payouts = []
    for agent_id in mission.crew_ids:
        agent = ds.read("agent", agent_id)
        if not agent:
            continue

        completed = sum(
            1 for t in mission.tasks
            if t.agent_id == agent_id
            and getattr(t.status, "value", t.status) == "complete")
        if not completed:
            continue

        xp = completed * cfg["xp_per_task"]
        gold = completed * cfg["gold_per_task"]
        if mission_complete:
            xp += cfg["mission_bonus_xp"]
            gold += cfg["mission_bonus_gold"]

        payout = apply_award(
            ds, agent, xp=xp, gold=gold, tx_type="mission_reward",
            reference=reference,
            note=f"{completed} task(s), mission {mission.id}")
        payout.tasks_completed = completed

        if mission_complete:
            done = agent.get("ratings", {}).get("missions_completed", 0) + 1
            ds.update("agent", agent_id,
                      {"ratings": {"missions_completed": done}})
            payout.agent.setdefault("ratings", {})["missions_completed"] = done

        payouts.append(payout)
    return payouts


def train(ds: DataStore, agent: dict, domain_slug: str, points: int) -> Payout:
    """
    Dojo: allocate unassigned skill points into a skill domain.
    Raises ValueError when the domain is unknown, the pool is short, or the
    domain cap would be exceeded.
    """
    domain = ds.get_reference_by_slug("skill_domain", domain_slug)
    if not domain:
        known = ", ".join(d["slug"] for d in ds.load_reference("skill_domain"))
        raise ValueError(f"Unknown skill domain '{domain_slug}' (known: {known})")
    if points < 1:
        raise ValueError("Training takes at least 1 skill point")

    sp = agent.get("economy", {}).get("skill_points", {})
    unassigned = sp.get("total", 0) - sp.get("assigned", 0)
    if points > unassigned:
        raise ValueError(
            f"Not enough unassigned skill points ({unassigned} available, "
            f"{points} requested) - rank up to earn more")

    skills = [dict(s) for s in agent.get("skills", [])]
    entry = next((s for s in skills if s.get("domain_id") == domain["id"]), None)
    if entry is None:
        entry = {"domain_id": domain["id"], "points": 0}
        skills.append(entry)
    cap = domain.get("max_points", 20)
    if entry["points"] + points > cap:
        raise ValueError(
            f"{domain['label']} caps at {cap} points "
            f"(currently {entry['points']})")
    entry["points"] += points

    updated = ds.update("agent", agent["id"], {
        "skills": skills,
        "economy": {"skill_points": {
            "total": sp.get("total", 0),
            "assigned": sp.get("assigned", 0) + points,
        }},
    })
    payout = Payout(agent=updated or agent)
    payout.notes.append(
        f"trained {domain['slug']} +{points} -> {entry['points']}/{cap}")
    return payout


def equip(ds: DataStore, agent: dict, tool_slug: str) -> Payout:
    """
    Depot: buy a tool with gold, or raise proficiency on one already
    carried (flat catalog cost per level, capped). Free-cost tools are
    issued without a ledger entry.
    """
    cfg = economy_config()
    tool = ds.get_reference_by_slug("tool", tool_slug)
    if not tool:
        raise ValueError(f"Unknown tool '{tool_slug}'")

    tools = [dict(t) for t in agent.get("tools", [])]
    owned = next((t for t in tools if t.get("tool_id") == tool["id"]), None)
    max_prof = cfg["max_tool_proficiency"]
    if owned and owned.get("proficiency", 1) >= max_prof:
        raise ValueError(
            f"{tool['label']} already at max proficiency ({max_prof})")

    cost = tool.get("cost", 0)
    gold = agent.get("economy", {}).get("gold", 0)
    if cost > gold:
        raise ValueError(
            f"{tool['label']} costs {cost}g - {agent['identity']['codename']} "
            f"has {gold}g")

    if owned:
        owned["proficiency"] = owned.get("proficiency", 1) + 1
        action = f"upgraded {tool['slug']} -> p{owned['proficiency']}"
        new_prof = owned["proficiency"]
    else:
        tools.append({
            "tool_id": tool["id"],
            "assigned_at": datetime.now(timezone.utc).isoformat(),
            "assigned_by": "depot",
            "proficiency": 1,
        })
        action = f"acquired {tool['slug']} (p1)"
        new_prof = 1

    updated = ds.update("agent", agent["id"], {
        "tools": tools,
        "economy": {"gold": gold - cost},
    })
    if cost > 0:
        _record_transaction(
            ds, agent, cost, "purchase",
            reference={"type": "manual", "id": tool["slug"]},
            notes=f"depot: {action}", outbound=True)

    payout = Payout(agent=updated or agent, gold=-cost)
    payout.notes.append(action + (f" for {cost}g" if cost else " (standard issue)"))
    return payout
