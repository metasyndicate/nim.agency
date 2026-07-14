"""
NIM Agency Remote Module

Provides secure remote deployment capabilities through SSH,
encrypted credential storage, and audited command execution.

Components:
- vault: Encrypted credential storage with Fernet
- keys: SSH key discovery and import
- ssh: AsyncSSH transport with connection pooling
- safety: Command classification for permission control
- protocol: High-level remote execution with audit logging

Usage:
    from lib.remote import (
        CredentialVault, VaultScope,
        SSHKeyDiscovery, scan_keys,
        SSHConnection, SSHConnectionConfig,
        CommandClassifier, classify_command,
        RemoteExecutor, SubstationPermissions
    )

    # Initialize vault
    vault = CredentialVault()
    vault.initialize("passphrase")

    # Discover and import SSH keys
    discovery = SSHKeyDiscovery()
    keys = discovery.scan()
    cred_id = discovery.import_to_vault(keys[0], vault)

    # Connect to remote host
    config = SSHConnectionConfig(
        hostname="server.example.com",
        username="admin",
        key_path="~/.ssh/id_ed25519"
    )

    async with SSHConnection(config) as conn:
        result = await conn.run("ls -la")
        print(result.stdout)
"""

# Vault - Encrypted credential storage
from .vault import (
    CredentialVault,
    VaultScope,
    VaultError,
    VaultLocked,
    VaultNotInitialized,
    CredentialNotFound,
    InvalidPassphrase,
    StoredCredential,
    get_vault,
)

# Keys - SSH key discovery
from .keys import (
    SSHKeyDiscovery,
    SSHKeyInfo,
    KeyType,
    KeyStatus,
    get_key_discovery,
    scan_keys,
)

# SSH - AsyncSSH transport
from .ssh import (
    SSHConnection,
    SSHConnectionConfig,
    ConnectionPool,
    CommandResult,
    FileTransferResult,
    HostKeyPolicy,
    HostKeyMismatch,
    ConnectionError,
    KnownHostsManager,
    get_connection_pool,
    run_remote,
)

# Safety - Command classification
from .safety import (
    CommandClassifier,
    CommandClassification,
    OperationIntent,
    get_classifier,
    classify_command,
    is_safe_command,
)

# Protocol - Remote execution with audit
from .protocol import (
    RemoteExecutor,
    SubstationPermissions,
    OperationRequest,
    OperationResponse,
    AuditLogger,
    DeploymentManager,
    PermissionDenied,
    ConfirmationRequired,
    get_executor,
)

# Ops Log - HQ messaging protocol
from .ops_log import (
    OpsMessage,
    OpsLogger,
    LogType,
    Protocol as OpsProtocol,
    OpStatus,
    OPS_LOG_HEADERS,
    get_ops_logger,
    log_ops,
)

__all__ = [
    # Vault
    "CredentialVault",
    "VaultScope",
    "VaultError",
    "VaultLocked",
    "VaultNotInitialized",
    "CredentialNotFound",
    "InvalidPassphrase",
    "StoredCredential",
    "get_vault",
    # Keys
    "SSHKeyDiscovery",
    "SSHKeyInfo",
    "KeyType",
    "KeyStatus",
    "get_key_discovery",
    "scan_keys",
    # SSH
    "SSHConnection",
    "SSHConnectionConfig",
    "ConnectionPool",
    "CommandResult",
    "FileTransferResult",
    "HostKeyPolicy",
    "HostKeyMismatch",
    "ConnectionError",
    "KnownHostsManager",
    "get_connection_pool",
    "run_remote",
    # Safety
    "CommandClassifier",
    "CommandClassification",
    "OperationIntent",
    "get_classifier",
    "classify_command",
    "is_safe_command",
    # Protocol
    "RemoteExecutor",
    "SubstationPermissions",
    "OperationRequest",
    "OperationResponse",
    "AuditLogger",
    "DeploymentManager",
    "PermissionDenied",
    "ConfirmationRequired",
    "get_executor",
    # Ops Log
    "OpsMessage",
    "OpsLogger",
    "LogType",
    "OpsProtocol",
    "OpStatus",
    "OPS_LOG_HEADERS",
    "get_ops_logger",
    "log_ops",
]
