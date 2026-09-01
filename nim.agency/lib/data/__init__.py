"""
NIM Agency Data Layer

Provides schema loading, validation, and CRUD operations
for the biomimetic agent datastore.
"""

from .schema import SchemaManager
from .crud import DataStore, get_datastore
from .generator import AgentGenerator
from .composer import AgentComposer, get_composer, compose_agent, compose_from_archetype
from .owl import OWL, assemble_crue, CrüeRoster
from .roster import RosterMatch, match_agents, draft_hire
from .economy import Payout, apply_award, award_mission, train, equip
from .identity import Enrollment, enroll, backfill, is_enrolled
from .project import (
    active_engagements, agent_bookings, apply_mission_outcome, assign_agent,
    availability_map, create_project, find_project, release_agent,
    transition_engagements,
)

# Re-export remote module for convenience
from ..remote import (
    CredentialVault,
    VaultScope,
    SSHKeyDiscovery,
    SSHConnection,
    SSHConnectionConfig,
    CommandClassifier,
    RemoteExecutor,
    SubstationPermissions,
)

__all__ = [
    # Data layer
    "SchemaManager",
    "DataStore",
    "get_datastore",
    "AgentGenerator",
    "AgentComposer",
    "get_composer",
    "compose_agent",
    "compose_from_archetype",
    "OWL",
    "assemble_crue",
    "CrüeRoster",
    "RosterMatch",
    "match_agents",
    "draft_hire",
    "Payout",
    "apply_award",
    "award_mission",
    "train",
    "equip",
    "Enrollment",
    "enroll",
    "backfill",
    "is_enrolled",
    "active_engagements",
    "agent_bookings",
    "apply_mission_outcome",
    "assign_agent",
    "availability_map",
    "create_project",
    "find_project",
    "release_agent",
    "transition_engagements",
    # Remote layer
    "CredentialVault",
    "VaultScope",
    "SSHKeyDiscovery",
    "SSHConnection",
    "SSHConnectionConfig",
    "CommandClassifier",
    "RemoteExecutor",
    "SubstationPermissions",
]
