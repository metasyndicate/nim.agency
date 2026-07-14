"""
SSH Transport - AsyncSSH-based remote execution

Provides async SSH connectivity with connection pooling,
jump host support, and trust-on-first-use host key verification.
"""

import asyncio
import json
import os
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, AsyncIterator, Callable, Union, Any
from enum import Enum

try:
    import asyncssh
    from asyncssh import SSHClientConnection, SSHKey, SSHCompletedProcess
    HAS_ASYNCSSH = True
except ImportError:
    HAS_ASYNCSSH = False
    SSHClientConnection = Any
    SSHKey = Any
    SSHCompletedProcess = Any


class HostKeyPolicy(Enum):
    """Host key verification policies."""
    TOFU = "tofu"          # Trust on first use, warn on change
    STRICT = "strict"       # Reject unknown hosts
    NONE = "none"          # Accept all (insecure)


class HostKeyMismatch(Exception):
    """Host key doesn't match stored fingerprint."""
    def __init__(self, hostname: str, expected: str, actual: str):
        self.hostname = hostname
        self.expected = expected
        self.actual = actual
        super().__init__(f"Host key mismatch for {hostname}: expected {expected}, got {actual}")


class ConnectionError(Exception):
    """SSH connection failed."""
    pass


@dataclass
class SSHConnectionConfig:
    """Configuration for an SSH connection."""
    hostname: str
    port: int = 22
    username: Optional[str] = None
    key_path: Optional[str] = None
    key_data: Optional[bytes] = None
    key_passphrase: Optional[str] = None
    password: Optional[str] = None
    jump_host: Optional["SSHConnectionConfig"] = None
    timeout: float = 30.0
    keepalive_interval: float = 60.0
    host_key_policy: HostKeyPolicy = HostKeyPolicy.TOFU

    @property
    def display_name(self) -> str:
        """Human-readable connection string."""
        user = self.username or os.environ.get("USER", "user")
        return f"{user}@{self.hostname}:{self.port}"


@dataclass
class CommandResult:
    """Result of a remote command execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    hostname: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def success(self) -> bool:
        return self.exit_code == 0


@dataclass
class FileTransferResult:
    """Result of a file transfer operation."""
    local_path: str
    remote_path: str
    direction: str  # "upload" or "download"
    bytes_transferred: int
    duration_ms: float
    success: bool
    error: Optional[str] = None


class KnownHostsManager:
    """
    Manages known host keys with TOFU verification.

    Stores host fingerprints in ~/.nim/agency/known_hosts
    """

    def __init__(self, hosts_file: Optional[Path] = None):
        if hosts_file is None:
            hosts_file = Path.home() / ".nim" / "agency" / "known_hosts"
        self.hosts_file = hosts_file
        self._hosts: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Load known hosts from file."""
        if self.hosts_file.exists():
            try:
                with open(self.hosts_file, 'r') as f:
                    self._hosts = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._hosts = {}

    def _save(self) -> None:
        """Save known hosts to file."""
        self.hosts_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.hosts_file, 'w') as f:
            json.dump(self._hosts, f, indent=2)
        if os.name != 'nt':
            os.chmod(self.hosts_file, 0o600)

    def get_key(self, hostname: str, port: int = 22) -> Optional[str]:
        """Get stored fingerprint for host."""
        key = f"{hostname}:{port}"
        return self._hosts.get(key)

    def store_key(self, hostname: str, port: int, fingerprint: str) -> None:
        """Store a host fingerprint."""
        key = f"{hostname}:{port}"
        self._hosts[key] = fingerprint
        self._hosts[f"{key}:first_seen"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def verify_or_store(
        self,
        hostname: str,
        port: int,
        fingerprint: str,
        policy: HostKeyPolicy
    ) -> bool:
        """
        Verify host key or store if new.

        Returns True if accepted, raises HostKeyMismatch if rejected.
        """
        stored = self.get_key(hostname, port)

        if stored is None:
            if policy == HostKeyPolicy.STRICT:
                raise HostKeyMismatch(hostname, "(no stored key)", fingerprint)
            # TOFU: store and accept
            self.store_key(hostname, port, fingerprint)
            return True

        if stored == fingerprint:
            return True

        if policy == HostKeyPolicy.NONE:
            # Accept anyway (insecure)
            return True

        # Key mismatch
        raise HostKeyMismatch(hostname, stored, fingerprint)

    def remove_key(self, hostname: str, port: int = 22) -> bool:
        """Remove a stored host key."""
        key = f"{hostname}:{port}"
        if key in self._hosts:
            del self._hosts[key]
            if f"{key}:first_seen" in self._hosts:
                del self._hosts[f"{key}:first_seen"]
            self._save()
            return True
        return False


class SSHConnection:
    """
    Async SSH connection wrapper.

    Provides command execution, file transfer, and streaming support.
    """

    def __init__(
        self,
        config: SSHConnectionConfig,
        known_hosts: Optional[KnownHostsManager] = None
    ):
        if not HAS_ASYNCSSH:
            raise ImportError("asyncssh required: pip install asyncssh")

        self.config = config
        self.known_hosts = known_hosts or KnownHostsManager()
        self._conn: Optional[SSHClientConnection] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._conn is not None

    async def connect(self) -> None:
        """Establish SSH connection."""
        if self.is_connected:
            return

        # Build connection options
        options: Dict[str, Any] = {
            "host": self.config.hostname,
            "port": self.config.port,
            "connect_timeout": self.config.timeout,
            "keepalive_interval": self.config.keepalive_interval,
        }

        if self.config.username:
            options["username"] = self.config.username

        # Key authentication
        if self.config.key_data:
            key = asyncssh.import_private_key(
                self.config.key_data,
                self.config.key_passphrase
            )
            options["client_keys"] = [key]
        elif self.config.key_path:
            options["client_keys"] = [self.config.key_path]
            if self.config.key_passphrase:
                options["passphrase"] = self.config.key_passphrase

        # Password authentication
        if self.config.password:
            options["password"] = self.config.password

        # Host key verification
        if self.config.host_key_policy == HostKeyPolicy.NONE:
            options["known_hosts"] = None
        else:
            # Custom verification callback
            options["known_hosts"] = None  # Disable asyncssh's built-in
            # We'll verify manually after connection

        try:
            # Handle jump host
            if self.config.jump_host:
                jump_conn = SSHConnection(self.config.jump_host, self.known_hosts)
                await jump_conn.connect()
                options["tunnel"] = jump_conn._conn

            self._conn = await asyncssh.connect(**options)

            # Verify host key with our TOFU policy
            if self.config.host_key_policy != HostKeyPolicy.NONE:
                await self._verify_host_key()

            self._connected = True

        except asyncssh.Error as e:
            raise ConnectionError(f"SSH connection failed: {e}") from e

    async def _verify_host_key(self) -> None:
        """Verify host key against known hosts."""
        if self._conn is None:
            return

        # Get server's host key
        host_key = self._conn.get_server_host_key()
        if host_key is None:
            return

        # Get fingerprint
        fingerprint = host_key.get_fingerprint()

        # Verify with known hosts manager
        self.known_hosts.verify_or_store(
            self.config.hostname,
            self.config.port,
            fingerprint,
            self.config.host_key_policy
        )

    async def disconnect(self) -> None:
        """Close SSH connection."""
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
        self._conn = None
        self._connected = False

    async def run(
        self,
        command: str,
        timeout: Optional[float] = None,
        check: bool = False
    ) -> CommandResult:
        """
        Execute a remote command.

        Args:
            command: Command to execute
            timeout: Command timeout in seconds
            check: Raise exception on non-zero exit

        Returns:
            CommandResult with output and exit code
        """
        if not self.is_connected:
            await self.connect()

        start = datetime.now(timezone.utc)

        try:
            result = await asyncio.wait_for(
                self._conn.run(command, check=check),
                timeout=timeout or 120.0
            )
            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            return CommandResult(
                command=command,
                exit_code=result.returncode or 0,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                duration_ms=duration,
                hostname=self.config.hostname
            )

        except asyncio.TimeoutError:
            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr="Command timed out",
                duration_ms=duration,
                hostname=self.config.hostname
            )
        except asyncssh.ProcessError as e:
            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return CommandResult(
                command=command,
                exit_code=e.returncode or 1,
                stdout=e.stdout or "",
                stderr=e.stderr or str(e),
                duration_ms=duration,
                hostname=self.config.hostname
            )

    async def run_streaming(
        self,
        command: str,
        stdout_callback: Optional[Callable[[str], None]] = None,
        stderr_callback: Optional[Callable[[str], None]] = None,
        timeout: Optional[float] = None
    ) -> CommandResult:
        """
        Execute command with streaming output.

        Args:
            command: Command to execute
            stdout_callback: Called for each stdout line
            stderr_callback: Called for each stderr line
            timeout: Command timeout

        Returns:
            CommandResult with full output
        """
        if not self.is_connected:
            await self.connect()

        start = datetime.now(timezone.utc)
        stdout_lines = []
        stderr_lines = []

        try:
            async with self._conn.create_process(command) as process:
                # Create tasks to read stdout and stderr
                async def read_stdout():
                    async for line in process.stdout:
                        stdout_lines.append(line)
                        if stdout_callback:
                            stdout_callback(line)

                async def read_stderr():
                    async for line in process.stderr:
                        stderr_lines.append(line)
                        if stderr_callback:
                            stderr_callback(line)

                await asyncio.wait_for(
                    asyncio.gather(read_stdout(), read_stderr()),
                    timeout=timeout or 600.0
                )

                await process.wait()
                exit_code = process.returncode or 0

        except asyncio.TimeoutError:
            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="".join(stdout_lines),
                stderr="".join(stderr_lines) + "\nCommand timed out",
                duration_ms=duration,
                hostname=self.config.hostname
            )

        duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        return CommandResult(
            command=command,
            exit_code=exit_code,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
            duration_ms=duration,
            hostname=self.config.hostname
        )

    async def upload(
        self,
        local_path: Union[str, Path],
        remote_path: str,
        recursive: bool = False
    ) -> FileTransferResult:
        """
        Upload file(s) to remote host.

        Args:
            local_path: Local file or directory
            remote_path: Remote destination path
            recursive: Recursively upload directory

        Returns:
            FileTransferResult
        """
        if not self.is_connected:
            await self.connect()

        local_path = Path(local_path)
        start = datetime.now(timezone.utc)

        try:
            async with self._conn.start_sftp_client() as sftp:
                if local_path.is_dir() and recursive:
                    await sftp.put(str(local_path), remote_path, recurse=True)
                else:
                    await sftp.put(str(local_path), remote_path)

            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            size = local_path.stat().st_size if local_path.is_file() else 0

            return FileTransferResult(
                local_path=str(local_path),
                remote_path=remote_path,
                direction="upload",
                bytes_transferred=size,
                duration_ms=duration,
                success=True
            )

        except (asyncssh.SFTPError, OSError) as e:
            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return FileTransferResult(
                local_path=str(local_path),
                remote_path=remote_path,
                direction="upload",
                bytes_transferred=0,
                duration_ms=duration,
                success=False,
                error=str(e)
            )

    async def download(
        self,
        remote_path: str,
        local_path: Union[str, Path],
        recursive: bool = False
    ) -> FileTransferResult:
        """
        Download file(s) from remote host.

        Args:
            remote_path: Remote file or directory
            local_path: Local destination path
            recursive: Recursively download directory

        Returns:
            FileTransferResult
        """
        if not self.is_connected:
            await self.connect()

        local_path = Path(local_path)
        start = datetime.now(timezone.utc)

        try:
            async with self._conn.start_sftp_client() as sftp:
                await sftp.get(remote_path, str(local_path), recurse=recursive)

            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            size = local_path.stat().st_size if local_path.is_file() else 0

            return FileTransferResult(
                local_path=str(local_path),
                remote_path=remote_path,
                direction="download",
                bytes_transferred=size,
                duration_ms=duration,
                success=True
            )

        except (asyncssh.SFTPError, OSError) as e:
            duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return FileTransferResult(
                local_path=str(local_path),
                remote_path=remote_path,
                direction="download",
                bytes_transferred=0,
                duration_ms=duration,
                success=False,
                error=str(e)
            )

    async def file_exists(self, remote_path: str) -> bool:
        """Check if remote file exists."""
        if not self.is_connected:
            await self.connect()

        try:
            async with self._conn.start_sftp_client() as sftp:
                await sftp.stat(remote_path)
                return True
        except asyncssh.SFTPError:
            return False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


class ConnectionPool:
    """
    Pool of reusable SSH connections.

    Manages connection lifecycle and provides efficient
    access to multiple hosts.
    """

    def __init__(
        self,
        max_connections_per_host: int = 3,
        connection_timeout: float = 30.0
    ):
        self.max_per_host = max_connections_per_host
        self.timeout = connection_timeout
        self._pools: Dict[str, List[SSHConnection]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._known_hosts = KnownHostsManager()

    def _get_pool_key(self, config: SSHConnectionConfig) -> str:
        """Generate unique key for connection pool."""
        return f"{config.username}@{config.hostname}:{config.port}"

    async def get_connection(self, config: SSHConnectionConfig) -> SSHConnection:
        """
        Get a connection from the pool.

        Creates new connection if pool is empty or at capacity.
        """
        key = self._get_pool_key(config)

        if key not in self._locks:
            self._locks[key] = asyncio.Lock()

        async with self._locks[key]:
            if key not in self._pools:
                self._pools[key] = []

            # Look for available connection
            for conn in self._pools[key]:
                if conn.is_connected:
                    return conn

            # Create new connection
            conn = SSHConnection(config, self._known_hosts)
            await conn.connect()
            self._pools[key].append(conn)
            return conn

    async def release_connection(self, conn: SSHConnection) -> None:
        """Release connection back to pool."""
        # Connection stays in pool for reuse
        pass

    async def close_all(self) -> None:
        """Close all pooled connections."""
        for pool in self._pools.values():
            for conn in pool:
                await conn.disconnect()
        self._pools.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_all()


# Module-level convenience
_default_pool: Optional[ConnectionPool] = None


def get_connection_pool() -> ConnectionPool:
    """Get the default connection pool."""
    global _default_pool
    if _default_pool is None:
        _default_pool = ConnectionPool()
    return _default_pool


async def run_remote(
    config: SSHConnectionConfig,
    command: str,
    timeout: Optional[float] = None
) -> CommandResult:
    """
    Convenience function to run a remote command.

    Uses the default connection pool.
    """
    pool = get_connection_pool()
    conn = await pool.get_connection(config)
    try:
        return await conn.run(command, timeout=timeout)
    finally:
        await pool.release_connection(conn)
