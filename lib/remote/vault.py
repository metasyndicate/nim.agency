"""
Credential Vault - Encrypted storage for SSH credentials

Provides secure storage for SSH keys and credentials using Fernet encryption.
Supports dual-scope storage: operator-local (~/.nim/agency/) and global (/etc/nim/agency/).

Security model:
- Master key derived from operator passphrase using PBKDF2 (600k iterations)
- Individual credentials encrypted with Fernet (AES-128-CBC + HMAC)
- Optional session caching with configurable timeout
- Restrictive file permissions (600)
"""

import os
import json
import uuid
import base64
import secrets
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Union
from dataclasses import dataclass, field, asdict
from enum import Enum

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


class VaultScope(Enum):
    """Storage scope for vault data."""
    OPERATOR = "operator"  # ~/.nim/agency/
    GLOBAL = "global"      # /etc/nim/agency/


class VaultError(Exception):
    """Base exception for vault operations."""
    pass


class VaultLocked(VaultError):
    """Vault is locked and requires unlock."""
    pass


class VaultNotInitialized(VaultError):
    """Vault has not been initialized with a master passphrase."""
    pass


class CredentialNotFound(VaultError):
    """Requested credential does not exist."""
    pass


class InvalidPassphrase(VaultError):
    """Passphrase is incorrect."""
    pass


@dataclass
class StoredCredential:
    """Represents a stored credential."""
    id: str
    name: str
    credential_type: str  # "ssh_key", "password", "token"
    scope: str
    created_at: str
    updated_at: str
    metadata: dict = field(default_factory=dict)
    # Encrypted data stored separately


@dataclass
class VaultSession:
    """Session token for vault unlock state."""
    token: str
    created_at: str
    expires_at: str
    scope: str


class CredentialVault:
    """
    Encrypted credential storage with dual-scope support.

    Usage:
        vault = CredentialVault()

        # First time setup
        if not vault.is_initialized():
            vault.initialize("my-secure-passphrase")

        # Unlock for session
        vault.unlock("my-secure-passphrase")

        # Store credential
        cred_id = vault.store_credential(
            name="prod-server-key",
            credential_type="ssh_key",
            data=private_key_bytes,
            metadata={"fingerprint": "SHA256:..."}
        )

        # Retrieve credential
        data = vault.get_credential(cred_id)

        # Lock when done
        vault.lock()
    """

    # PBKDF2 iterations (OWASP recommended minimum)
    KDF_ITERATIONS = 600_000
    KDF_SALT_SIZE = 32
    SESSION_TIMEOUT_HOURS = 8

    def __init__(self, scope: VaultScope = VaultScope.OPERATOR):
        """
        Initialize vault.

        Args:
            scope: Storage scope (OPERATOR or GLOBAL)
        """
        if not HAS_CRYPTOGRAPHY:
            raise ImportError("cryptography library required: pip install cryptography")

        self.scope = scope
        self.base_path = self._get_base_path(scope)
        self.vault_path = self.base_path / "vault"
        self.credentials_path = self.vault_path / "credentials"

        self._fernet: Optional[Fernet] = None
        self._session: Optional[VaultSession] = None

    @staticmethod
    def _get_base_path(scope: VaultScope) -> Path:
        """Get base path for scope."""
        if scope == VaultScope.OPERATOR:
            return Path.home() / ".nim" / "agency"
        else:
            return Path("/etc/nim/agency")

    def _ensure_directories(self) -> None:
        """Create vault directories with proper permissions."""
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.credentials_path.mkdir(parents=True, exist_ok=True)

        # Set restrictive permissions (owner only)
        if os.name != 'nt':  # Unix
            os.chmod(self.vault_path, 0o700)
            os.chmod(self.credentials_path, 0o700)

    def _derive_key(self, passphrase: str, salt: bytes) -> bytes:
        """Derive encryption key from passphrase using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.KDF_ITERATIONS,
        )
        return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))

    def _get_master_key_path(self) -> Path:
        """Get path to master key file."""
        return self.vault_path / ".master.key"

    def _get_session_path(self) -> Path:
        """Get path to session file."""
        return self.base_path / ".session"

    def is_initialized(self) -> bool:
        """Check if vault has been initialized."""
        return self._get_master_key_path().exists()

    def is_unlocked(self) -> bool:
        """Check if vault is currently unlocked."""
        if self._fernet is not None:
            return True

        # Check for valid session
        session = self._load_session()
        if session:
            return True

        return False

    def initialize(self, passphrase: str) -> None:
        """
        Initialize vault with master passphrase.

        Creates the master key file encrypted with the passphrase.

        Args:
            passphrase: Master passphrase for vault

        Raises:
            VaultError: If vault already initialized
        """
        if self.is_initialized():
            raise VaultError("Vault already initialized")

        self._ensure_directories()

        # Generate salt and derive key
        salt = secrets.token_bytes(self.KDF_SALT_SIZE)
        derived_key = self._derive_key(passphrase, salt)

        # Generate the actual master key
        master_key = Fernet.generate_key()

        # Encrypt master key with derived key
        fernet = Fernet(derived_key)
        encrypted_master = fernet.encrypt(master_key)

        # Store salt + encrypted master key
        master_data = {
            "version": 1,
            "salt": base64.b64encode(salt).decode(),
            "encrypted_key": base64.b64encode(encrypted_master).decode(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kdf_iterations": self.KDF_ITERATIONS
        }

        master_path = self._get_master_key_path()
        with open(master_path, 'w') as f:
            json.dump(master_data, f, indent=2)

        # Set restrictive permissions
        if os.name != 'nt':
            os.chmod(master_path, 0o600)

        # Auto-unlock after init
        self._fernet = Fernet(master_key)

    def unlock(self, passphrase: str, create_session: bool = True) -> None:
        """
        Unlock vault with passphrase.

        Args:
            passphrase: Master passphrase
            create_session: Create session token for auto-unlock

        Raises:
            VaultNotInitialized: If vault not initialized
            InvalidPassphrase: If passphrase is wrong
        """
        if not self.is_initialized():
            raise VaultNotInitialized("Vault not initialized - call initialize() first")

        # Load master key data
        master_path = self._get_master_key_path()
        with open(master_path, 'r') as f:
            master_data = json.load(f)

        salt = base64.b64decode(master_data["salt"])
        encrypted_key = base64.b64decode(master_data["encrypted_key"])
        iterations = master_data.get("kdf_iterations", self.KDF_ITERATIONS)

        # Derive key from passphrase
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        derived_key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))

        # Decrypt master key
        try:
            fernet = Fernet(derived_key)
            master_key = fernet.decrypt(encrypted_key)
        except InvalidToken:
            raise InvalidPassphrase("Invalid passphrase")

        self._fernet = Fernet(master_key)

        if create_session:
            self._create_session()

    def lock(self, clear_session: bool = True) -> None:
        """
        Lock vault.

        Args:
            clear_session: Also clear session token
        """
        self._fernet = None

        if clear_session:
            self._clear_session()

    def _create_session(self) -> None:
        """Create session token for auto-unlock."""
        if self._fernet is None:
            return

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.SESSION_TIMEOUT_HOURS)

        # Generate session token (encrypted master key reference)
        token = secrets.token_urlsafe(32)

        # Store encrypted reference
        session_data = {
            "token": token,
            "key_check": self._fernet.encrypt(b"vault_session_check").decode(),
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "scope": self.scope.value
        }

        session_path = self._get_session_path()
        with open(session_path, 'w') as f:
            json.dump(session_data, f, indent=2)

        if os.name != 'nt':
            os.chmod(session_path, 0o600)

        self._session = VaultSession(
            token=token,
            created_at=now.isoformat(),
            expires_at=expires.isoformat(),
            scope=self.scope.value
        )

    def _load_session(self) -> Optional[VaultSession]:
        """Load and validate session token."""
        session_path = self._get_session_path()
        if not session_path.exists():
            return None

        try:
            with open(session_path, 'r') as f:
                data = json.load(f)

            # Check expiry
            expires = datetime.fromisoformat(data["expires_at"])
            if datetime.now(timezone.utc) > expires:
                self._clear_session()
                return None

            # If we have a valid session but no fernet, we need passphrase
            # The session just indicates the vault was recently unlocked
            return VaultSession(**{k: data[k] for k in ["token", "created_at", "expires_at", "scope"]})
        except (json.JSONDecodeError, KeyError):
            self._clear_session()
            return None

    def _clear_session(self) -> None:
        """Clear session token."""
        session_path = self._get_session_path()
        if session_path.exists():
            session_path.unlink()
        self._session = None

    def _require_unlock(self) -> None:
        """Ensure vault is unlocked."""
        if self._fernet is None:
            raise VaultLocked("Vault is locked - call unlock() first")

    def store_credential(
        self,
        name: str,
        credential_type: str,
        data: Union[bytes, str],
        metadata: Optional[dict] = None
    ) -> str:
        """
        Store a credential in the vault.

        Args:
            name: Human-readable name for credential
            credential_type: Type ("ssh_key", "password", "token")
            data: Credential data (will be encrypted)
            metadata: Optional metadata (stored unencrypted)

        Returns:
            Credential ID (UUID)
        """
        self._require_unlock()
        self._ensure_directories()

        cred_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Encrypt data
        if isinstance(data, str):
            data = data.encode()
        encrypted_data = self._fernet.encrypt(data)

        # Create credential record
        credential = StoredCredential(
            id=cred_id,
            name=name,
            credential_type=credential_type,
            scope=self.scope.value,
            created_at=now,
            updated_at=now,
            metadata=metadata or {}
        )

        # Store encrypted data
        cred_path = self.credentials_path / f"{cred_id}.enc"
        cred_data = {
            **asdict(credential),
            "encrypted_data": base64.b64encode(encrypted_data).decode()
        }

        with open(cred_path, 'w') as f:
            json.dump(cred_data, f, indent=2)

        if os.name != 'nt':
            os.chmod(cred_path, 0o600)

        return cred_id

    def get_credential(self, cred_id: str) -> bytes:
        """
        Retrieve decrypted credential data.

        Args:
            cred_id: Credential ID

        Returns:
            Decrypted credential data
        """
        self._require_unlock()

        cred_path = self.credentials_path / f"{cred_id}.enc"
        if not cred_path.exists():
            raise CredentialNotFound(f"Credential {cred_id} not found")

        with open(cred_path, 'r') as f:
            cred_data = json.load(f)

        encrypted_data = base64.b64decode(cred_data["encrypted_data"])
        return self._fernet.decrypt(encrypted_data)

    def get_credential_info(self, cred_id: str) -> StoredCredential:
        """
        Get credential metadata (without decrypting data).

        Args:
            cred_id: Credential ID

        Returns:
            StoredCredential metadata
        """
        cred_path = self.credentials_path / f"{cred_id}.enc"
        if not cred_path.exists():
            raise CredentialNotFound(f"Credential {cred_id} not found")

        with open(cred_path, 'r') as f:
            cred_data = json.load(f)

        return StoredCredential(
            id=cred_data["id"],
            name=cred_data["name"],
            credential_type=cred_data["credential_type"],
            scope=cred_data["scope"],
            created_at=cred_data["created_at"],
            updated_at=cred_data["updated_at"],
            metadata=cred_data.get("metadata", {})
        )

    def list_credentials(self) -> list[StoredCredential]:
        """
        List all stored credentials (metadata only).

        Returns:
            List of StoredCredential objects
        """
        if not self.credentials_path.exists():
            return []

        credentials = []
        for cred_file in self.credentials_path.glob("*.enc"):
            try:
                cred_id = cred_file.stem
                credentials.append(self.get_credential_info(cred_id))
            except (json.JSONDecodeError, KeyError):
                continue

        return sorted(credentials, key=lambda c: c.name)

    def delete_credential(self, cred_id: str) -> bool:
        """
        Delete a credential.

        Args:
            cred_id: Credential ID

        Returns:
            True if deleted, False if not found
        """
        self._require_unlock()

        cred_path = self.credentials_path / f"{cred_id}.enc"
        if not cred_path.exists():
            return False

        cred_path.unlink()
        return True

    def update_credential(
        self,
        cred_id: str,
        name: Optional[str] = None,
        data: Optional[Union[bytes, str]] = None,
        metadata: Optional[dict] = None
    ) -> StoredCredential:
        """
        Update a credential.

        Args:
            cred_id: Credential ID
            name: New name (optional)
            data: New data (optional)
            metadata: New metadata (merged with existing)

        Returns:
            Updated StoredCredential
        """
        self._require_unlock()

        cred_path = self.credentials_path / f"{cred_id}.enc"
        if not cred_path.exists():
            raise CredentialNotFound(f"Credential {cred_id} not found")

        with open(cred_path, 'r') as f:
            cred_data = json.load(f)

        now = datetime.now(timezone.utc).isoformat()

        if name:
            cred_data["name"] = name

        if data:
            if isinstance(data, str):
                data = data.encode()
            encrypted_data = self._fernet.encrypt(data)
            cred_data["encrypted_data"] = base64.b64encode(encrypted_data).decode()

        if metadata:
            cred_data["metadata"] = {**cred_data.get("metadata", {}), **metadata}

        cred_data["updated_at"] = now

        with open(cred_path, 'w') as f:
            json.dump(cred_data, f, indent=2)

        return StoredCredential(
            id=cred_data["id"],
            name=cred_data["name"],
            credential_type=cred_data["credential_type"],
            scope=cred_data["scope"],
            created_at=cred_data["created_at"],
            updated_at=cred_data["updated_at"],
            metadata=cred_data.get("metadata", {})
        )

    def change_passphrase(self, old_passphrase: str, new_passphrase: str) -> None:
        """
        Change the vault master passphrase.

        Args:
            old_passphrase: Current passphrase
            new_passphrase: New passphrase
        """
        # First verify old passphrase
        self.unlock(old_passphrase, create_session=False)

        # Get current master key
        master_key = base64.urlsafe_b64decode(self._fernet._signing_key + self._fernet._encryption_key)

        # Re-encrypt with new passphrase
        new_salt = secrets.token_bytes(self.KDF_SALT_SIZE)
        new_derived_key = self._derive_key(new_passphrase, new_salt)

        new_fernet = Fernet(new_derived_key)
        encrypted_master = new_fernet.encrypt(master_key)

        # Update master key file
        master_data = {
            "version": 1,
            "salt": base64.b64encode(new_salt).decode(),
            "encrypted_key": base64.b64encode(encrypted_master).decode(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kdf_iterations": self.KDF_ITERATIONS
        }

        master_path = self._get_master_key_path()
        with open(master_path, 'w') as f:
            json.dump(master_data, f, indent=2)

        # Clear session (must re-authenticate with new passphrase)
        self._clear_session()

    def export_credential(self, cred_id: str, export_passphrase: str) -> str:
        """
        Export a credential with separate encryption for transfer.

        Args:
            cred_id: Credential ID
            export_passphrase: Passphrase to encrypt export

        Returns:
            Base64-encoded encrypted export
        """
        self._require_unlock()

        # Get credential data
        data = self.get_credential(cred_id)
        info = self.get_credential_info(cred_id)

        # Create export package
        export_data = {
            "name": info.name,
            "credential_type": info.credential_type,
            "metadata": info.metadata,
            "data": base64.b64encode(data).decode()
        }

        # Encrypt with export passphrase
        salt = secrets.token_bytes(16)
        export_key = self._derive_key(export_passphrase, salt)
        export_fernet = Fernet(export_key)

        encrypted = export_fernet.encrypt(json.dumps(export_data).encode())

        # Package with salt
        package = {
            "version": 1,
            "salt": base64.b64encode(salt).decode(),
            "data": base64.b64encode(encrypted).decode()
        }

        return base64.b64encode(json.dumps(package).encode()).decode()

    def import_credential(self, export_data: str, export_passphrase: str) -> str:
        """
        Import a credential from encrypted export.

        Args:
            export_data: Base64-encoded encrypted export
            export_passphrase: Passphrase used for export

        Returns:
            New credential ID
        """
        self._require_unlock()

        # Decode package
        package = json.loads(base64.b64decode(export_data))
        salt = base64.b64decode(package["salt"])
        encrypted = base64.b64decode(package["data"])

        # Decrypt with export passphrase
        export_key = self._derive_key(export_passphrase, salt)
        export_fernet = Fernet(export_key)

        try:
            decrypted = export_fernet.decrypt(encrypted)
            cred_data = json.loads(decrypted)
        except InvalidToken:
            raise InvalidPassphrase("Invalid export passphrase")

        # Store in vault
        return self.store_credential(
            name=cred_data["name"],
            credential_type=cred_data["credential_type"],
            data=base64.b64decode(cred_data["data"]),
            metadata=cred_data.get("metadata")
        )


# Module-level convenience functions
_default_vault: Optional[CredentialVault] = None


def get_vault(scope: VaultScope = VaultScope.OPERATOR) -> CredentialVault:
    """Get or create the default vault instance."""
    global _default_vault
    if _default_vault is None or _default_vault.scope != scope:
        _default_vault = CredentialVault(scope)
    return _default_vault
