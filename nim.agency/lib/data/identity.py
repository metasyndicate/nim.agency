"""
Identity - agent enrollment: primary keypair issuance and the depot vault.

Every agent is issued a primary ed25519 keypair at enrollment - the authc/
authz identity anchor it presents at all credential checks. Private keys are
passphrase-free by design (automation prohibits interactive unlock) and
defer to host/OS protection: the vault and key files carry minimal
operator-only permissions. The AGENCY keeps central custody - key material
lives only in the vault, never in the agent record (the record carries the
public half and fingerprint). The primary keypair is immutable: no rotation,
only revoke-and-supersede.

Vault layout (the local "crypt" host, per the depot's access charter):
    data/agency/hive/depot/.vault/      0700
        <dogtag>/                       0700
            id_ed25519                  0600
            id_ed25519.pub              0644
            manifest.json               0600
"""

import getpass
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import PROJECT_ROOT
from ..remote.keys import KeyType, SSHKeyDiscovery
from .crud import DataStore, get_datastore

ALGORITHM = KeyType.ED25519
KEY_FILENAME = f"id_{ALGORITHM.value}"


def vault_root() -> Path:
    return PROJECT_ROOT / "data" / "agency" / "hive" / "depot" / ".vault"


def org_id(agent: dict) -> str:
    """Badge number: NIM-<tag>, falling back to a uuid fragment."""
    tag = agent.get("identity", {}).get("tag")
    if isinstance(tag, int):
        return f"NIM-{tag:04d}"
    return f"NIM-{str(agent.get('id', ''))[:4].upper()}"


def is_enrolled(agent: dict) -> bool:
    """Record-level enrollment check (fingerprint present, not revoked)."""
    crypto = agent.get("crypto") or {}
    return bool(crypto.get("fingerprint")) and crypto.get("status") != "revoked"


@dataclass
class Enrollment:
    """One issued badge - everything the ceremony discloses."""
    agent: dict
    vault_path: Path
    fingerprint: str
    public_key: str
    already_enrolled: bool = False

    def render(self) -> str:
        ident = self.agent["identity"]
        verb = "BADGE ON FILE" if self.already_enrolled else "BADGE ISSUED"
        return "\n".join([
            f"{verb}  {org_id(self.agent)}  {ident['dogtag']}",
            f"  agent:    {ident['name']} ({ident['codename']})",
            f"  keypair:  {ALGORITHM.value}  {self.fingerprint}",
            f"  vault:    {self.vault_path.relative_to(PROJECT_ROOT)}/",
        ])


def _agent_vault_dir(agent: dict) -> Path:
    """Subvault path for an agent; dogtag-named, disambiguated with a uuid
    fragment when a generated name collides with another agent's."""
    dogtag = agent["identity"]["dogtag"]
    path = vault_root() / dogtag
    manifest = path / "manifest.json"
    if manifest.exists():
        try:
            with open(manifest) as f:
                if json.load(f).get("agent_id") != agent["id"]:
                    path = vault_root() / f"{dogtag}.{str(agent['id'])[:8]}"
        except (json.JSONDecodeError, OSError):
            path = vault_root() / f"{dogtag}.{str(agent['id'])[:8]}"
    return path


def enroll(ds: DataStore, agent: dict) -> Enrollment:
    """
    Issue the agent's primary keypair and register it in the vault.

    Idempotent: an already-enrolled agent gets its existing badge back
    (primary keys are immutable - there is deliberately no re-issue path).
    Raises RuntimeError when key generation fails (e.g. no ssh-keygen).
    """
    if is_enrolled(agent):
        crypto = agent["crypto"]
        return Enrollment(
            agent=agent,
            vault_path=PROJECT_ROOT / crypto.get(
                "vault_path", str(_agent_vault_dir(agent))),
            fingerprint=crypto.get("fingerprint", "?"),
            public_key=crypto.get("public_key", ""),
            already_enrolled=True,
        )

    root = vault_root()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)

    subvault = _agent_vault_dir(agent)
    subvault.mkdir(mode=0o700, exist_ok=True)
    os.chmod(subvault, 0o700)

    dogtag = agent["identity"]["dogtag"]
    key_path = subvault / KEY_FILENAME
    if key_path.exists():
        # Orphaned material without a matching record claim - refuse to
        # overwrite; the operator decides what it is first
        raise RuntimeError(
            f"Unclaimed key material already present at {key_path} - "
            "resolve manually before enrolling")

    key_info = SSHKeyDiscovery().generate_key(
        key_type=ALGORITHM,
        comment=f"{dogtag}@nim.agency",
        output_path=key_path,
    )
    os.chmod(key_path, 0o600)
    pub_path = subvault / f"{KEY_FILENAME}.pub"
    if pub_path.exists():
        os.chmod(pub_path, 0o644)
    public_key = pub_path.read_text().strip() if pub_path.exists() else ""

    now = datetime.now(timezone.utc).isoformat()
    operator = getpass.getuser()
    manifest = {
        "agent_id": agent["id"],
        "dogtag": dogtag,
        "codename": agent["identity"]["codename"],
        "org_id": org_id(agent),
        "algorithm": ALGORITHM.value,
        "fingerprint": key_info.fingerprint,
        "public_key": public_key,
        "status": "active",
        "issued_at": now,
        "issued_by": operator,
        "events": [{"type": "issued", "at": now, "by": operator}],
    }
    manifest_path = subvault / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    os.chmod(manifest_path, 0o600)

    updated = ds.update("agent", agent["id"], {"crypto": {
        "public_key": public_key,
        "fingerprint": key_info.fingerprint,
        "key_type": ALGORITHM.value,
        "created_at": now,
        "status": "active",
        "vault_path": str(subvault.relative_to(PROJECT_ROOT)),
    }})

    return Enrollment(
        agent=updated or agent,
        vault_path=subvault,
        fingerprint=key_info.fingerprint,
        public_key=public_key,
    )


def try_enroll(ds: DataStore, agent: dict) -> Optional[Enrollment]:
    """Enrollment that degrades gracefully - agent creation must not die
    because ssh-keygen is missing. Returns None on failure."""
    try:
        return enroll(ds, agent)
    except (RuntimeError, OSError) as e:
        print(f"\033[93mWARN: enrollment deferred for "
              f"{agent['identity']['codename']}: {e}\033[0m")
        return None


def backfill(ds: Optional[DataStore] = None) -> tuple[list[Enrollment], list[dict]]:
    """Enroll every unenrolled agent on the roster.

    Returns (enrollments actually issued, agents already enrolled).
    """
    ds = ds or get_datastore()
    issued, existing = [], []
    for agent in ds.query("agent").data:
        if is_enrolled(agent):
            existing.append(agent)
            continue
        issued.append(enroll(ds, agent))
    return issued, existing
