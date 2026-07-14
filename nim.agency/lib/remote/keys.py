"""
SSH Key Discovery - Find and import local SSH keys

Scans standard SSH key locations, parses key metadata,
and provides import capabilities to the credential vault.
"""

import os
import re
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


class KeyType(Enum):
    """SSH key types."""
    RSA = "rsa"
    ED25519 = "ed25519"
    ECDSA = "ecdsa"
    DSA = "dsa"
    UNKNOWN = "unknown"


class KeyStatus(Enum):
    """Key discovery status."""
    AVAILABLE = "available"
    PASSPHRASE_PROTECTED = "passphrase_protected"
    INVALID = "invalid"
    IMPORTED = "imported"


@dataclass
class SSHKeyInfo:
    """Metadata about a discovered SSH key."""
    path: str
    filename: str
    key_type: KeyType
    bits: int
    fingerprint: str
    comment: str
    has_passphrase: bool
    public_key_path: Optional[str]
    status: KeyStatus
    error: Optional[str] = None

    @property
    def display_name(self) -> str:
        """Human-readable display name."""
        name = self.comment if self.comment else self.filename
        type_str = self.key_type.value.upper()
        return f"{name} ({type_str} {self.bits})"


class SSHKeyDiscovery:
    """
    Discover and manage SSH keys on the local system.

    Scans standard locations for SSH keys and provides metadata
    extraction and import capabilities.

    Usage:
        discovery = SSHKeyDiscovery()
        keys = discovery.scan()

        for key in keys:
            print(f"{key.display_name}: {key.fingerprint}")

        # Import to vault
        from lib.remote.vault import CredentialVault
        vault = CredentialVault()
        vault.unlock("passphrase")
        cred_id = discovery.import_to_vault(keys[0], vault)
    """

    # Standard SSH key locations
    DEFAULT_PATHS = {
        "posix": [
            Path.home() / ".ssh",
            Path("/etc/ssh"),
        ],
        "nt": [
            Path.home() / ".ssh",
            Path(os.environ.get("USERPROFILE", "")) / ".ssh",
        ]
    }

    # Key filename patterns
    KEY_PATTERNS = [
        re.compile(r"^id_rsa$"),
        re.compile(r"^id_ed25519$"),
        re.compile(r"^id_ecdsa$"),
        re.compile(r"^id_dsa$"),
        re.compile(r"^id_.*$"),
        re.compile(r".*_rsa$"),
        re.compile(r".*_ed25519$"),
        re.compile(r".*_ecdsa$"),
    ]

    def __init__(self, additional_paths: Optional[List[Path]] = None):
        """
        Initialize key discovery.

        Args:
            additional_paths: Extra directories to scan
        """
        self.paths = list(self.DEFAULT_PATHS.get(os.name, self.DEFAULT_PATHS["posix"]))
        if additional_paths:
            self.paths.extend(additional_paths)

    def scan(self, include_invalid: bool = False) -> List[SSHKeyInfo]:
        """
        Scan for SSH keys.

        Args:
            include_invalid: Include keys that failed validation

        Returns:
            List of discovered keys
        """
        discovered = []

        for base_path in self.paths:
            if not base_path.exists():
                continue

            for key_file in self._find_key_files(base_path):
                key_info = self._analyze_key(key_file)
                if key_info:
                    if include_invalid or key_info.status != KeyStatus.INVALID:
                        discovered.append(key_info)

        # Sort by type and name
        return sorted(discovered, key=lambda k: (k.key_type.value, k.filename))

    def _find_key_files(self, directory: Path) -> List[Path]:
        """Find potential key files in directory."""
        keys = []

        if not directory.is_dir():
            return keys

        try:
            for item in directory.iterdir():
                if item.is_file():
                    # Skip public keys, known_hosts, config
                    if item.suffix == ".pub":
                        continue
                    if item.name in ("known_hosts", "authorized_keys", "config"):
                        continue

                    # Check filename patterns
                    for pattern in self.KEY_PATTERNS:
                        if pattern.match(item.name):
                            keys.append(item)
                            break
                    else:
                        # Check file content for key header
                        if self._is_private_key(item):
                            keys.append(item)
        except PermissionError:
            pass

        return keys

    def _is_private_key(self, path: Path) -> bool:
        """Check if file looks like a private key."""
        try:
            with open(path, 'r') as f:
                first_line = f.readline()
                return "PRIVATE KEY" in first_line or "OPENSSH PRIVATE KEY" in first_line
        except (UnicodeDecodeError, PermissionError):
            return False

    def _analyze_key(self, key_path: Path) -> Optional[SSHKeyInfo]:
        """Analyze a key file and extract metadata."""
        # Check for corresponding public key
        pub_path = Path(str(key_path) + ".pub")
        has_pub = pub_path.exists()

        # Determine key type from file content
        key_type = self._detect_key_type(key_path)

        # Try to get fingerprint and metadata
        fingerprint = ""
        bits = 0
        comment = ""
        has_passphrase = False
        error = None
        status = KeyStatus.AVAILABLE

        # Use ssh-keygen to get info
        try:
            if has_pub:
                # Get info from public key
                result = subprocess.run(
                    ["ssh-keygen", "-l", "-f", str(pub_path)],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    fingerprint, bits, comment = self._parse_keygen_output(result.stdout)
            else:
                # Try to get info from private key (may prompt for passphrase)
                result = subprocess.run(
                    ["ssh-keygen", "-l", "-f", str(key_path)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    input=""  # Empty passphrase attempt
                )
                if result.returncode == 0:
                    fingerprint, bits, comment = self._parse_keygen_output(result.stdout)

            # Check if key is passphrase-protected
            has_passphrase = self._check_passphrase(key_path)
            if has_passphrase:
                status = KeyStatus.PASSPHRASE_PROTECTED

        except subprocess.TimeoutExpired:
            status = KeyStatus.INVALID
            error = "Timeout analyzing key"
        except FileNotFoundError:
            # ssh-keygen not available
            pass
        except Exception as e:
            status = KeyStatus.INVALID
            error = str(e)

        return SSHKeyInfo(
            path=str(key_path),
            filename=key_path.name,
            key_type=key_type,
            bits=bits,
            fingerprint=fingerprint,
            comment=comment,
            has_passphrase=has_passphrase,
            public_key_path=str(pub_path) if has_pub else None,
            status=status,
            error=error
        )

    def _detect_key_type(self, key_path: Path) -> KeyType:
        """Detect key type from file content."""
        try:
            with open(key_path, 'r') as f:
                content = f.read(500)  # Read first 500 bytes

            if "RSA PRIVATE KEY" in content:
                return KeyType.RSA
            elif "OPENSSH PRIVATE KEY" in content:
                # Could be any type, check filename hint
                if "ed25519" in key_path.name:
                    return KeyType.ED25519
                elif "ecdsa" in key_path.name:
                    return KeyType.ECDSA
                # Default modern key
                return KeyType.ED25519
            elif "EC PRIVATE KEY" in content:
                return KeyType.ECDSA
            elif "DSA PRIVATE KEY" in content:
                return KeyType.DSA
            else:
                return KeyType.UNKNOWN
        except (UnicodeDecodeError, PermissionError):
            return KeyType.UNKNOWN

    def _parse_keygen_output(self, output: str) -> tuple[str, int, str]:
        """Parse ssh-keygen -l output."""
        # Format: "256 SHA256:xxx comment (TYPE)"
        # or: "2048 SHA256:xxx comment (RSA)"
        parts = output.strip().split()
        if len(parts) >= 2:
            bits = int(parts[0]) if parts[0].isdigit() else 0
            fingerprint = parts[1] if len(parts) > 1 else ""

            # Comment is everything between fingerprint and type marker
            comment = ""
            if len(parts) > 2:
                # Find type marker (last word in parentheses)
                type_idx = -1
                for i, p in enumerate(parts):
                    if p.startswith("(") and p.endswith(")"):
                        type_idx = i
                        break

                if type_idx > 2:
                    comment = " ".join(parts[2:type_idx])
                elif type_idx == -1 and len(parts) > 2:
                    comment = " ".join(parts[2:])

            return fingerprint, bits, comment

        return "", 0, ""

    def _check_passphrase(self, key_path: Path) -> bool:
        """Check if a key is passphrase-protected."""
        try:
            # Try to parse key with empty passphrase
            result = subprocess.run(
                ["ssh-keygen", "-y", "-P", "", "-f", str(key_path)],
                capture_output=True,
                text=True,
                timeout=5
            )
            # If it succeeds with empty passphrase, not protected
            return result.returncode != 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # Assume protected if we can't check
            return True

    def get_public_key(self, key_info: SSHKeyInfo) -> Optional[str]:
        """
        Get the public key for a private key.

        Args:
            key_info: Key info from scan

        Returns:
            Public key string or None
        """
        if key_info.public_key_path:
            try:
                with open(key_info.public_key_path, 'r') as f:
                    return f.read().strip()
            except (PermissionError, FileNotFoundError):
                pass

        # Try to derive from private key
        try:
            result = subprocess.run(
                ["ssh-keygen", "-y", "-f", key_info.path],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return None

    def read_private_key(self, key_info: SSHKeyInfo) -> bytes:
        """
        Read private key contents.

        Args:
            key_info: Key info from scan

        Returns:
            Private key bytes
        """
        with open(key_info.path, 'rb') as f:
            return f.read()

    def import_to_vault(
        self,
        key_info: SSHKeyInfo,
        vault: "CredentialVault",  # Forward reference
        key_passphrase: Optional[str] = None
    ) -> str:
        """
        Import an SSH key to the credential vault.

        Args:
            key_info: Key info from scan
            vault: Unlocked credential vault
            key_passphrase: Passphrase if key is protected

        Returns:
            Credential ID in vault
        """
        # Read private key
        private_key = self.read_private_key(key_info)

        # Get public key if available
        public_key = self.get_public_key(key_info)

        # Build metadata
        metadata = {
            "key_type": key_info.key_type.value,
            "bits": key_info.bits,
            "fingerprint": key_info.fingerprint,
            "original_path": key_info.path,
            "has_passphrase": key_info.has_passphrase,
        }
        if key_info.comment:
            metadata["comment"] = key_info.comment
        if public_key:
            metadata["public_key"] = public_key
        if key_passphrase:
            # Store key passphrase encrypted with the key data
            metadata["key_passphrase_hint"] = "stored"

        # Combine private key and passphrase if provided
        if key_passphrase and key_info.has_passphrase:
            # Store passphrase alongside key (vault encrypts both)
            import json
            data = json.dumps({
                "private_key": private_key.decode('utf-8', errors='replace'),
                "passphrase": key_passphrase
            }).encode()
        else:
            data = private_key

        return vault.store_credential(
            name=key_info.display_name,
            credential_type="ssh_key",
            data=data,
            metadata=metadata
        )

    def generate_key(
        self,
        key_type: KeyType = KeyType.ED25519,
        bits: int = 0,
        comment: str = "",
        passphrase: Optional[str] = None,
        output_path: Optional[Path] = None
    ) -> SSHKeyInfo:
        """
        Generate a new SSH key pair.

        Args:
            key_type: Key type (ED25519 recommended)
            bits: Key bits (0 for default)
            comment: Key comment
            passphrase: Optional passphrase
            output_path: Path for new key (default: ~/.ssh/id_{type})

        Returns:
            Info about generated key
        """
        # Determine output path
        if output_path is None:
            ssh_dir = Path.home() / ".ssh"
            ssh_dir.mkdir(exist_ok=True)
            output_path = ssh_dir / f"id_{key_type.value}"

        # Build ssh-keygen command
        cmd = ["ssh-keygen", "-t", key_type.value, "-f", str(output_path)]

        if bits > 0 and key_type in (KeyType.RSA, KeyType.ECDSA):
            cmd.extend(["-b", str(bits)])

        if comment:
            cmd.extend(["-C", comment])

        if passphrase:
            cmd.extend(["-N", passphrase])
        else:
            cmd.extend(["-N", ""])

        # Generate key
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            raise RuntimeError(f"Key generation failed: {result.stderr}")

        # Analyze the new key
        key_info = self._analyze_key(output_path)
        if key_info is None:
            raise RuntimeError("Failed to analyze generated key")

        return key_info


# Module-level convenience
_default_discovery: Optional[SSHKeyDiscovery] = None


def get_key_discovery() -> SSHKeyDiscovery:
    """Get default key discovery instance."""
    global _default_discovery
    if _default_discovery is None:
        _default_discovery = SSHKeyDiscovery()
    return _default_discovery


def scan_keys() -> List[SSHKeyInfo]:
    """Convenience function to scan for keys."""
    return get_key_discovery().scan()
