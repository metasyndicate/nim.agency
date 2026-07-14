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
