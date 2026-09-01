"""
Project - stateful scoped work units (aggregate roots).

A project REFERENCES agents, substations, and paths - it never copies
them. Engagements are the only new stateful relation: one row per agent
per project, carrying assignment_status lifecycle state. A released row
(released_at set) is history, not state.
"""

from datetime import datetime, timezone
from typing import Optional

from .crud import DataStore

# assignment_status ids (engagement lifecycle vocabulary - reused, not
# reinvented). Cycle flow: queued -> swarming (dispatch) -> review (mission
# complete) or smoked (mission failed; non-terminal suspension). completed/
# failed are reserved for release-time outcomes.
ENGAGEMENT_QUEUED = 2
ENGAGEMENT_SWARMING = 4
ENGAGEMENT_REVIEW = 7
ENGAGEMENT_SMOKED = 11

# project_status ids
PROJECT_DRAFT = 1
PROJECT_ACTIVE = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_project(ds: DataStore, ref: str) -> Optional[dict]:
    """Resolve a project by slug, exact id, or unique id prefix."""
    projects = ds.query("project").data
    for project in projects:
        if project.get("slug") == ref or project.get("id") == ref:
            return project
    prefixed = [p for p in projects if p.get("id", "").startswith(ref)]
    return prefixed[0] if len(prefixed) == 1 else None


def project_status(ds: DataStore, project: dict) -> dict:
    return ds.get_reference_by_id("project_status", project.get("status_id")) or {}


def engagement_status(ds: DataStore, engagement: dict) -> dict:
    return ds.get_reference_by_id(
        "assignment_status", engagement.get("status_id")) or {}


def active_engagements(project: dict) -> list[dict]:
    """Engagement rows that have not been released."""
    return [e for e in project.get("engagements", [])
            if not e.get("released_at")]


def next_objective_id(project: dict) -> int:
    return max((o.get("id", 0) for o in project.get("objectives", [])),
               default=0) + 1


def agent_bookings(
    ds: DataStore, agent_id: str, exclude_project_id: Optional[str] = None
) -> list[dict]:
    """Active bookings for an agent across all projects: unreleased rows
    whose assignment_status is non-terminal. A queued engagement books the
    agent - assignment reserves, dispatch activates.

    Returns [{"project": ..., "engagement": ...}].
    """
    bookings = []
    for project in ds.query("project").data:
        if project.get("id") == exclude_project_id:
            continue
        for row in active_engagements(project):
            if row.get("agent_id") != agent_id:
                continue
            if not engagement_status(ds, row).get("is_terminal"):
                bookings.append({"project": project, "engagement": row})
    return bookings


def availability_map(ds: DataStore) -> dict[str, list[str]]:
    """agent_id -> [project slugs] for every active booking, in one scan.
    Agents absent from the map are available."""
    booked: dict[str, list[str]] = {}
    for project in ds.query("project").data:
        for row in active_engagements(project):
            if engagement_status(ds, row).get("is_terminal"):
                continue
            booked.setdefault(row["agent_id"], []).append(project["slug"])
    return booked


def transition_engagements(
    ds: DataStore,
    project: dict,
    status_id: int,
    agent_ids: Optional[list[str]] = None,
) -> int:
    """Move active engagement rows to a new assignment_status. Limited to
    agent_ids when given. Returns the number of rows moved."""
    rows = list(project.get("engagements", []))
    moved = 0
    for row in rows:
        if row.get("released_at"):
            continue
        if agent_ids is not None and row.get("agent_id") not in agent_ids:
            continue
        if row.get("status_id") != status_id:
            row["status_id"] = status_id
            moved += 1
    if moved:
        ds.update("project", project["id"], {"engagements": rows})
        project["engagements"] = rows
    return moved


def apply_mission_outcome(
    ds: DataStore,
    project: dict,
    mission_status: str,
    agent_ids: Optional[list[str]] = None,
) -> tuple[int, int]:
    """Drive engagement state from a mission outcome: complete -> review
    (awaiting operator between cycles); anything else -> smoked (suspended
    pending operator intervention). Returns (rows_moved, status_id)."""
    status_id = (ENGAGEMENT_REVIEW if mission_status == "complete"
                 else ENGAGEMENT_SMOKED)
    return transition_engagements(ds, project, status_id, agent_ids), status_id


def create_project(
    ds: DataStore,
    slug: str,
    title: str,
    *,
    description: str = "",
    priority: int = 5,
    worktree: Optional[str] = None,
    writable: bool = False,
    substation_id: Optional[str] = None,
    created_by: str = "operator",
) -> dict:
    """Charter a project in draft status. Slugs are the relational anchor
    and must be unique."""
    if find_project(ds, slug):
        raise ValueError(f"Project slug already exists: {slug}")

    worktrees = []
    if worktree:
        worktrees.append({"path": worktree, "writable": writable, "primary": True})

    permissions = ["read", "write"] if writable else ["read"]
    project = {
        "slug": slug,
        "title": title,
        "description": description,
        "status_id": PROJECT_DRAFT,
        "priority": priority,
        "crue_id": None,
        "engagements": [],
        "host_binding": {
            "substation_id": substation_id,
            "worktrees": worktrees,
            "required_permissions": permissions,
        },
        "objectives": [],
        "specifications": [],
        "directives": [],
        "artifacts": [],
        "policy": {
            "max_cycles": None,
            "read_only": not writable,
            "max_concurrent_engagements": 1,
        },
        "journal": [],
        "owl_id": None,
        "created_by": created_by,
    }
    return ds.create("project", project)


def assign_agent(
    ds: DataStore,
    project: dict,
    agent: dict,
    role: str = "specialist",
    responsibilities: Optional[list[str]] = None,
) -> dict:
    """Engage an agent on a project. One active engagement per agent per
    project, and bookings elsewhere count against the target project's
    max_concurrent_engagements cap - availability is derived, never stored."""
    for row in active_engagements(project):
        if row.get("agent_id") == agent.get("id"):
            raise ValueError(
                f"{agent['identity']['codename']} is already engaged on "
                f"{project['slug']} as {row.get('role', '?')}")

    cap = (project.get("policy") or {}).get("max_concurrent_engagements", 1)
    bookings = agent_bookings(ds, agent["id"],
                              exclude_project_id=project.get("id"))
    if len(bookings) + 1 > cap:
        slugs = ", ".join(b["project"]["slug"] for b in bookings)
        raise ValueError(
            f"{agent['identity']['codename']} is BOOKED ({slugs}) - cap is "
            f"{cap}; release an engagement or raise it: "
            f"nim.agency project edit {project['slug']} --cap N")

    engagement = {
        "agent_id": agent["id"],
        "role": role,
        "responsibilities": responsibilities or [],
        "status_id": ENGAGEMENT_QUEUED,
        "assigned_at": _now(),
        "released_at": None,
    }
    rows = list(project.get("engagements", [])) + [engagement]
    ds.update("project", project["id"], {"engagements": rows})
    project["engagements"] = rows
    return engagement


def release_agent(ds: DataStore, project: dict, agent: dict) -> dict:
    """End an agent's active engagement. The row stays in the project as
    history; only released_at changes hands."""
    rows = list(project.get("engagements", []))
    for row in rows:
        if row.get("agent_id") == agent.get("id") and not row.get("released_at"):
            row["released_at"] = _now()
            ds.update("project", project["id"], {"engagements": rows})
            project["engagements"] = rows
            return row
    raise ValueError(
        f"{agent['identity']['codename']} has no active engagement on "
        f"{project['slug']}")
