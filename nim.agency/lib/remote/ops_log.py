"""
HQ Operations Log - Standard messaging format for agent<->HQ communications

Provides a standardized logging format for remote operations that can be:
- Written to CSV/delimited files for portability
- Sent to HQ via HTTP endpoint
- Used to build infrastructure boards/maps

Message Format (URL):
    http://hq.host:88888/agency?agent=1234&log=access&protocol=ssh&source=2.3.4.5&dest=1.2.3.4&msg="connected"

CSV Format:
    timestamp,agent_id,log_type,protocol,source,dest,status,duration_ms,message,metadata
"""

import os
import csv
import json
import uuid
import socket
import platform
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from enum import Enum
from urllib.parse import urlencode, parse_qs, urlparse


class LogType(Enum):
    """Types of operations to log."""
    ACCESS = "access"      # Connection established
    COMMAND = "command"    # Command executed
    TRANSFER = "transfer"  # File transfer
    DEPLOY = "deploy"      # Agent deployment
    HEALTH = "health"      # Health check
    ERROR = "error"        # Error occurred
    AUTH = "auth"          # Authentication event
    ALERT = "alert"        # Alert/warning


class Protocol(Enum):
    """Communication protocols."""
    SSH = "ssh"
    SFTP = "sftp"
    HTTP = "http"
    HTTPS = "https"
    WS = "ws"
    GRPC = "grpc"
    LOCAL = "local"


class OpStatus(Enum):
    """Operation status codes."""
    OK = "ok"
    FAIL = "fail"
    TIMEOUT = "timeout"
    DENIED = "denied"
    PENDING = "pending"


@dataclass
class OpsMessage:
    """
    Standard operations message for HQ communications.

    Captures all relevant context for an operation including:
    - Who (agent, operator)
    - What (log type, command/action)
    - Where (source, dest)
    - When (timestamp)
    - How (protocol, status)
    """
    # Core identifiers
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Who
    agent_id: str = ""
    agent_tag: int = 0
    agent_codename: str = ""
    operator: str = ""

    # What
    log_type: str = "command"
    action: str = ""
    message: str = ""

    # Where
    source_host: str = ""
    source_ip: str = ""
    dest_host: str = ""
    dest_ip: str = ""
    dest_port: int = 0

    # How
    protocol: str = "ssh"
    status: str = "ok"
    exit_code: Optional[int] = None
    duration_ms: float = 0

    # Context
    substation_id: str = ""
    substation_name: str = ""
    org_name: str = ""
    unit_name: str = ""

    # Metadata (flexible key-value)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        d = asdict(self)
        d["metadata"] = json.dumps(d["metadata"]) if d["metadata"] else ""
        return d

    def to_csv_row(self) -> List[str]:
        """Convert to CSV row values."""
        return [
            self.timestamp,
            self.agent_id,
            str(self.agent_tag),
            self.agent_codename,
            self.operator,
            self.log_type,
            self.action,
            self.protocol,
            self.source_host,
            self.source_ip,
            self.dest_host,
            self.dest_ip,
            str(self.dest_port),
            self.status,
            str(self.exit_code) if self.exit_code is not None else "",
            f"{self.duration_ms:.2f}",
            self.substation_id,
            self.substation_name,
            self.org_name,
            self.unit_name,
            self.message,
            json.dumps(self.metadata) if self.metadata else "",
        ]

    def to_url_params(self) -> str:
        """Convert to URL query string for HQ endpoint."""
        params = {
            "id": self.id,
            "ts": self.timestamp,
            "agent": self.agent_id,
            "tag": self.agent_tag,
            "codename": self.agent_codename,
            "op": self.operator,
            "log": self.log_type,
            "action": self.action,
            "proto": self.protocol,
            "src": self.source_ip or self.source_host,
            "dst": self.dest_ip or self.dest_host,
            "port": self.dest_port,
            "status": self.status,
            "exit": self.exit_code if self.exit_code is not None else "",
            "ms": f"{self.duration_ms:.0f}",
            "sub": self.substation_name,
            "org": self.org_name,
            "msg": self.message,
        }
        # Filter empty values
        params = {k: v for k, v in params.items() if v != "" and v is not None}
        return urlencode(params)

    def to_hq_url(self, hq_host: str = "localhost", hq_port: int = 8888) -> str:
        """Build full HQ URL for this message."""
        return f"http://{hq_host}:{hq_port}/agency?{self.to_url_params()}"

    @classmethod
    def from_url(cls, url: str) -> "OpsMessage":
        """Parse message from URL query string."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        # parse_qs returns lists, get first value
        def get(key: str, default: str = "") -> str:
            return params.get(key, [default])[0]

        return cls(
            id=get("id", str(uuid.uuid4())),
            timestamp=get("ts", datetime.now(timezone.utc).isoformat()),
            agent_id=get("agent"),
            agent_tag=int(get("tag", "0")),
            agent_codename=get("codename"),
            operator=get("op"),
            log_type=get("log", "command"),
            action=get("action"),
            protocol=get("proto", "ssh"),
            source_ip=get("src"),
            dest_ip=get("dst"),
            dest_port=int(get("port", "0")),
            status=get("status", "ok"),
            exit_code=int(get("exit")) if get("exit") else None,
            duration_ms=float(get("ms", "0")),
            substation_name=get("sub"),
            org_name=get("org"),
            message=get("msg"),
        )


# CSV column headers
OPS_LOG_HEADERS = [
    "timestamp",
    "agent_id",
    "agent_tag",
    "agent_codename",
    "operator",
    "log_type",
    "action",
    "protocol",
    "source_host",
    "source_ip",
    "dest_host",
    "dest_ip",
    "dest_port",
    "status",
    "exit_code",
    "duration_ms",
    "substation_id",
    "substation_name",
    "org_name",
    "unit_name",
    "message",
    "metadata",
]


class OpsLogger:
    """
    Operations logger for CSV/delimited output.

    Writes standardized operation logs that can be used for:
    - Audit trails
    - Infrastructure mapping
    - Metrics/analytics
    - Agent activity boards

    Usage:
        logger = OpsLogger()

        # Log an SSH access
        logger.log_access(
            agent_id="abc-123",
            agent_tag=42,
            source_ip="10.0.0.5",
            dest_host="server.example.com",
            dest_ip="192.168.1.100",
            protocol="ssh",
            message="Connection established"
        )

        # Log a command execution
        logger.log_command(
            agent_id="abc-123",
            command="ls -la",
            dest_host="server.example.com",
            exit_code=0,
            duration_ms=125.5
        )
    """

    def __init__(
        self,
        log_path: Optional[Path] = None,
        delimiter: str = ",",
        org_name: str = "",
        unit_name: str = "",
        substation_name: str = ""
    ):
        """
        Initialize logger.

        Args:
            log_path: Path to log file (default: ~/.nim/agency/ops.log)
            delimiter: Field delimiter (default: comma for CSV)
            org_name: Default organization name
            unit_name: Default unit name
            substation_name: Default substation name
        """
        if log_path is None:
            log_path = Path.home() / ".nim" / "agency" / "ops.log"

        self.log_path = log_path
        self.delimiter = delimiter
        self.org_name = org_name
        self.unit_name = unit_name
        self.substation_name = substation_name

        # System info for source context
        self._hostname = socket.gethostname()
        self._platform = platform.system()

        self._ensure_log_file()

    def _ensure_log_file(self) -> None:
        """Create log file with headers if it doesn't exist."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.log_path.exists():
            with open(self.log_path, 'w', newline='') as f:
                writer = csv.writer(f, delimiter=self.delimiter)
                writer.writerow(OPS_LOG_HEADERS)

            if os.name != 'nt':
                os.chmod(self.log_path, 0o600)

    def _get_local_ip(self) -> str:
        """Get local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _write_message(self, msg: OpsMessage) -> None:
        """Write message to log file."""
        with open(self.log_path, 'a', newline='') as f:
            writer = csv.writer(f, delimiter=self.delimiter)
            writer.writerow(msg.to_csv_row())

    def log(self, msg: OpsMessage) -> OpsMessage:
        """
        Log an operations message.

        Args:
            msg: OpsMessage to log

        Returns:
            The logged message (with any auto-filled fields)
        """
        # Auto-fill defaults
        if not msg.source_host:
            msg.source_host = self._hostname
        if not msg.source_ip:
            msg.source_ip = self._get_local_ip()
        if not msg.org_name:
            msg.org_name = self.org_name
        if not msg.unit_name:
            msg.unit_name = self.unit_name
        if not msg.substation_name:
            msg.substation_name = self.substation_name

        self._write_message(msg)
        return msg

    def log_access(
        self,
        agent_id: str,
        dest_host: str,
        dest_ip: str = "",
        dest_port: int = 22,
        protocol: str = "ssh",
        agent_tag: int = 0,
        agent_codename: str = "",
        operator: str = "",
        source_ip: str = "",
        status: str = "ok",
        message: str = "",
        **metadata
    ) -> OpsMessage:
        """Log an access/connection event."""
        msg = OpsMessage(
            agent_id=agent_id,
            agent_tag=agent_tag,
            agent_codename=agent_codename,
            operator=operator,
            log_type=LogType.ACCESS.value,
            action="connect",
            protocol=protocol,
            source_ip=source_ip,
            dest_host=dest_host,
            dest_ip=dest_ip,
            dest_port=dest_port,
            status=status,
            message=message or "Connection established",
            metadata=metadata
        )
        return self.log(msg)

    def log_command(
        self,
        agent_id: str,
        command: str,
        dest_host: str,
        exit_code: int = 0,
        duration_ms: float = 0,
        dest_ip: str = "",
        protocol: str = "ssh",
        agent_tag: int = 0,
        agent_codename: str = "",
        operator: str = "",
        source_ip: str = "",
        status: str = "",
        message: str = "",
        **metadata
    ) -> OpsMessage:
        """Log a command execution."""
        if not status:
            status = "ok" if exit_code == 0 else "fail"

        msg = OpsMessage(
            agent_id=agent_id,
            agent_tag=agent_tag,
            agent_codename=agent_codename,
            operator=operator,
            log_type=LogType.COMMAND.value,
            action=command[:200],  # Truncate long commands
            protocol=protocol,
            source_ip=source_ip,
            dest_host=dest_host,
            dest_ip=dest_ip,
            exit_code=exit_code,
            duration_ms=duration_ms,
            status=status,
            message=message or f"exit={exit_code}",
            metadata=metadata
        )
        return self.log(msg)

    def log_transfer(
        self,
        agent_id: str,
        dest_host: str,
        direction: str,  # "upload" or "download"
        local_path: str,
        remote_path: str,
        bytes_transferred: int = 0,
        duration_ms: float = 0,
        success: bool = True,
        dest_ip: str = "",
        agent_tag: int = 0,
        agent_codename: str = "",
        operator: str = "",
        source_ip: str = "",
        **metadata
    ) -> OpsMessage:
        """Log a file transfer."""
        msg = OpsMessage(
            agent_id=agent_id,
            agent_tag=agent_tag,
            agent_codename=agent_codename,
            operator=operator,
            log_type=LogType.TRANSFER.value,
            action=f"{direction}:{remote_path}",
            protocol="sftp",
            source_ip=source_ip,
            dest_host=dest_host,
            dest_ip=dest_ip,
            duration_ms=duration_ms,
            status="ok" if success else "fail",
            message=f"{bytes_transferred} bytes",
            metadata={"local_path": local_path, "remote_path": remote_path, **metadata}
        )
        return self.log(msg)

    def log_deploy(
        self,
        agent_id: str,
        dest_host: str,
        deployment_id: str,
        remote_path: str,
        success: bool = True,
        dest_ip: str = "",
        agent_tag: int = 0,
        agent_codename: str = "",
        operator: str = "",
        source_ip: str = "",
        message: str = "",
        **metadata
    ) -> OpsMessage:
        """Log an agent deployment."""
        msg = OpsMessage(
            agent_id=agent_id,
            agent_tag=agent_tag,
            agent_codename=agent_codename,
            operator=operator,
            log_type=LogType.DEPLOY.value,
            action=f"deploy:{deployment_id}",
            protocol="sftp",
            source_ip=source_ip,
            dest_host=dest_host,
            dest_ip=dest_ip,
            status="ok" if success else "fail",
            message=message or f"Deployed to {remote_path}",
            metadata={"deployment_id": deployment_id, "remote_path": remote_path, **metadata}
        )
        return self.log(msg)

    def log_health(
        self,
        agent_id: str,
        dest_host: str,
        is_healthy: bool,
        dest_ip: str = "",
        agent_tag: int = 0,
        agent_codename: str = "",
        operator: str = "",
        source_ip: str = "",
        message: str = "",
        **metadata
    ) -> OpsMessage:
        """Log a health check."""
        msg = OpsMessage(
            agent_id=agent_id,
            agent_tag=agent_tag,
            agent_codename=agent_codename,
            operator=operator,
            log_type=LogType.HEALTH.value,
            action="health_check",
            protocol="ssh",
            source_ip=source_ip,
            dest_host=dest_host,
            dest_ip=dest_ip,
            status="ok" if is_healthy else "fail",
            message=message or ("healthy" if is_healthy else "unhealthy"),
            metadata=metadata
        )
        return self.log(msg)

    def log_error(
        self,
        agent_id: str,
        dest_host: str,
        error: str,
        action: str = "",
        protocol: str = "ssh",
        dest_ip: str = "",
        agent_tag: int = 0,
        agent_codename: str = "",
        operator: str = "",
        source_ip: str = "",
        **metadata
    ) -> OpsMessage:
        """Log an error."""
        msg = OpsMessage(
            agent_id=agent_id,
            agent_tag=agent_tag,
            agent_codename=agent_codename,
            operator=operator,
            log_type=LogType.ERROR.value,
            action=action,
            protocol=protocol,
            source_ip=source_ip,
            dest_host=dest_host,
            dest_ip=dest_ip,
            status="fail",
            message=error,
            metadata=metadata
        )
        return self.log(msg)

    def get_recent(self, limit: int = 100) -> List[Dict[str, str]]:
        """Get recent log entries as dictionaries."""
        entries = []
        try:
            with open(self.log_path, 'r', newline='') as f:
                reader = csv.DictReader(f, delimiter=self.delimiter)
                for row in reader:
                    entries.append(dict(row))
        except (FileNotFoundError, csv.Error):
            return []

        return entries[-limit:]

    def get_by_agent(self, agent_id: str, limit: int = 100) -> List[Dict[str, str]]:
        """Get log entries for a specific agent."""
        return [e for e in self.get_recent(limit * 10) if e.get("agent_id") == agent_id][-limit:]

    def get_by_host(self, hostname: str, limit: int = 100) -> List[Dict[str, str]]:
        """Get log entries for a specific host."""
        return [
            e for e in self.get_recent(limit * 10)
            if hostname in (e.get("dest_host", ""), e.get("dest_ip", ""))
        ][-limit:]

    def get_infrastructure_map(self) -> Dict[str, Dict]:
        """
        Build infrastructure map from logs.

        Returns a dictionary mapping hosts to their activity:
        {
            "host.example.com": {
                "ip": "192.168.1.100",
                "agents": ["agent-1", "agent-2"],
                "last_access": "2026-04-08T...",
                "access_count": 42,
                "protocols": ["ssh", "sftp"]
            }
        }
        """
        infra: Dict[str, Dict] = {}

        for entry in self.get_recent(10000):
            host = entry.get("dest_host") or entry.get("dest_ip")
            if not host:
                continue

            if host not in infra:
                infra[host] = {
                    "ip": entry.get("dest_ip", ""),
                    "hostname": entry.get("dest_host", ""),
                    "agents": set(),
                    "last_access": "",
                    "access_count": 0,
                    "protocols": set(),
                    "statuses": {"ok": 0, "fail": 0}
                }

            infra[host]["agents"].add(entry.get("agent_id", ""))
            infra[host]["protocols"].add(entry.get("protocol", ""))
            infra[host]["access_count"] += 1
            infra[host]["last_access"] = entry.get("timestamp", "")

            status = entry.get("status", "ok")
            if status in infra[host]["statuses"]:
                infra[host]["statuses"][status] += 1

        # Convert sets to lists for JSON serialization
        for host in infra:
            infra[host]["agents"] = list(infra[host]["agents"])
            infra[host]["protocols"] = list(infra[host]["protocols"])

        return infra


# Module-level convenience
_default_logger: Optional[OpsLogger] = None


def get_ops_logger(
    org_name: str = "",
    unit_name: str = "",
    substation_name: str = ""
) -> OpsLogger:
    """Get the default operations logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = OpsLogger(
            org_name=org_name,
            unit_name=unit_name,
            substation_name=substation_name
        )
    return _default_logger


def log_ops(msg: OpsMessage) -> OpsMessage:
    """Convenience function to log an operation."""
    return get_ops_logger().log(msg)
