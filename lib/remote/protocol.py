"""
Remote Operation Protocol - Execute commands with safety and audit

Provides a high-level interface for remote command execution with
built-in safety checks, permission management, and audit logging.
"""

import asyncio
import json
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Callable, Any
from enum import Enum
import uuid

from .ssh import SSHConnection, SSHConnectionConfig, CommandResult, FileTransferResult
from .safety import CommandClassifier, CommandClassification, OperationIntent


class PermissionDenied(Exception):
    """Operation requires permission that was not granted."""
    pass


class ConfirmationRequired(Exception):
    """Operation requires user confirmation."""
    def __init__(self, message: str, operation: "OperationRequest"):
        self.message = message
        self.operation = operation
        super().__init__(message)


@dataclass
class SubstationPermissions:
    """Permission configuration for a substation."""
    read_enabled: bool = True
    write_enabled: bool = False
    deploy_enabled: bool = False
    requires_confirmation: bool = True
    allowed_commands: List[str] = field(default_factory=list)  # Whitelist
    blocked_commands: List[str] = field(default_factory=list)  # Blacklist

    def allows(self, intent: OperationIntent) -> bool:
        """Check if intent is allowed."""
        if intent == OperationIntent.READ:
            return self.read_enabled
        elif intent == OperationIntent.WRITE:
            return self.write_enabled
        elif intent == OperationIntent.DEPLOY:
            return self.deploy_enabled
        return False


@dataclass
class OperationRequest:
    """Request for a remote operation."""
    id: str
    command: str
    hostname: str
    intent: OperationIntent
    classification: CommandClassification
    requested_at: str
    requested_by: str
    confirmed: bool = False
    confirmed_at: Optional[str] = None
    confirmed_by: Optional[str] = None


@dataclass
class OperationResponse:
    """Response from a remote operation."""
    request: OperationRequest
    result: Optional[CommandResult]
    success: bool
    error: Optional[str] = None
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AuditLogger:
    """
    Logs all remote operations for audit trail.

    Writes to ~/.nim/agency/remote_operations.json
    """

    def __init__(self, log_path: Optional[Path] = None):
        if log_path is None:
            log_path = Path.home() / ".nim" / "agency" / "remote_operations.json"
        self.log_path = log_path
        self._ensure_log_file()

    def _ensure_log_file(self) -> None:
        """Create log file if it doesn't exist."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            with open(self.log_path, 'w') as f:
                json.dump({"operations": []}, f)
            if os.name != 'nt':
                os.chmod(self.log_path, 0o600)

    def log_operation(self, response: OperationResponse) -> None:
        """Log an operation to the audit trail."""
        try:
            with open(self.log_path, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            data = {"operations": []}

        # Build log entry
        entry = {
            "id": response.request.id,
            "command": response.request.command,
            "hostname": response.request.hostname,
            "intent": response.request.intent.value,
            "requested_at": response.request.requested_at,
            "requested_by": response.request.requested_by,
            "completed_at": response.completed_at,
            "success": response.success,
            "exit_code": response.result.exit_code if response.result else None,
            "duration_ms": response.result.duration_ms if response.result else None,
            "error": response.error,
            "dangerous_patterns": response.request.classification.dangerous_patterns,
            "confirmed": response.request.confirmed,
            "confirmed_by": response.request.confirmed_by,
        }

        data["operations"].append(entry)

        # Keep last 1000 entries
        if len(data["operations"]) > 1000:
            data["operations"] = data["operations"][-1000:]

        with open(self.log_path, 'w') as f:
            json.dump(data, f, indent=2)

    def get_recent_operations(
        self,
        hostname: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict]:
        """Get recent operations, optionally filtered by host."""
        try:
            with open(self.log_path, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

        operations = data.get("operations", [])

        if hostname:
            operations = [op for op in operations if op.get("hostname") == hostname]

        return list(reversed(operations[-limit:]))


class RemoteExecutor:
    """
    High-level remote command executor with safety controls.

    Integrates command classification, permission checking,
    confirmation prompts, and audit logging.

    Usage:
        executor = RemoteExecutor()

        # Configure permissions for a substation
        permissions = SubstationPermissions(
            read_enabled=True,
            write_enabled=False
        )

        # Create connection config
        config = SSHConnectionConfig(hostname="server.example.com")

        # Execute command
        response = await executor.execute(
            connection=SSHConnection(config),
            command="ls -la",
            permissions=permissions,
            operator="admin"
        )
    """

    def __init__(
        self,
        classifier: Optional[CommandClassifier] = None,
        audit_logger: Optional[AuditLogger] = None,
        confirmation_callback: Optional[Callable[[OperationRequest], bool]] = None
    ):
        """
        Initialize executor.

        Args:
            classifier: Command safety classifier
            audit_logger: Audit log writer
            confirmation_callback: Function to prompt for confirmation
        """
        self.classifier = classifier or CommandClassifier()
        self.audit = audit_logger or AuditLogger()
        self.confirmation_callback = confirmation_callback

    def _create_request(
        self,
        command: str,
        hostname: str,
        operator: str
    ) -> OperationRequest:
        """Create an operation request with classification."""
        classification = self.classifier.classify(command)

        return OperationRequest(
            id=str(uuid.uuid4()),
            command=command,
            hostname=hostname,
            intent=classification.intent,
            classification=classification,
            requested_at=datetime.now(timezone.utc).isoformat(),
            requested_by=operator
        )

    def _check_permissions(
        self,
        request: OperationRequest,
        permissions: SubstationPermissions
    ) -> None:
        """
        Check if operation is permitted.

        Raises:
            PermissionDenied: If operation not allowed
        """
        # Check intent permission
        if not permissions.allows(request.intent):
            raise PermissionDenied(
                f"{request.intent.value.upper()} operations not permitted on this substation"
            )

        # Check command blacklist
        base_cmd = request.command.split()[0] if request.command else ""
        if base_cmd in permissions.blocked_commands:
            raise PermissionDenied(f"Command '{base_cmd}' is blocked on this substation")

        # Check whitelist (if configured)
        if permissions.allowed_commands and base_cmd not in permissions.allowed_commands:
            raise PermissionDenied(f"Command '{base_cmd}' not in allowed list")

    async def _request_confirmation(
        self,
        request: OperationRequest,
        permissions: SubstationPermissions
    ) -> bool:
        """
        Request user confirmation for dangerous operations.

        Returns True if confirmed, False otherwise.
        """
        if not permissions.requires_confirmation:
            return True

        if request.classification.is_safe:
            return True

        if self.confirmation_callback:
            return self.confirmation_callback(request)

        # No callback - raise exception for caller to handle
        raise ConfirmationRequired(
            f"Operation requires confirmation: {request.command}",
            request
        )

    async def execute(
        self,
        connection: SSHConnection,
        command: str,
        permissions: SubstationPermissions,
        operator: str,
        timeout: Optional[float] = None,
        confirmed: bool = False
    ) -> OperationResponse:
        """
        Execute a remote command with safety checks.

        Args:
            connection: SSH connection to use
            command: Command to execute
            permissions: Substation permission config
            operator: Operator username
            timeout: Command timeout
            confirmed: Skip confirmation if True

        Returns:
            OperationResponse with result
        """
        # Create request
        request = self._create_request(command, connection.config.hostname, operator)

        try:
            # Check permissions
            self._check_permissions(request, permissions)

            # Request confirmation if needed
            if not confirmed and request.requires_permission:
                is_confirmed = await self._request_confirmation(request, permissions)
                if not is_confirmed:
                    return OperationResponse(
                        request=request,
                        result=None,
                        success=False,
                        error="Operation cancelled - confirmation denied"
                    )
                request.confirmed = True
                request.confirmed_at = datetime.now(timezone.utc).isoformat()
                request.confirmed_by = operator

            # Execute command
            result = await connection.run(command, timeout=timeout)

            response = OperationResponse(
                request=request,
                result=result,
                success=result.success
            )

        except PermissionDenied as e:
            response = OperationResponse(
                request=request,
                result=None,
                success=False,
                error=str(e)
            )
        except ConfirmationRequired:
            raise  # Let caller handle
        except Exception as e:
            response = OperationResponse(
                request=request,
                result=None,
                success=False,
                error=str(e)
            )

        # Log operation
        self.audit.log_operation(response)

        return response

    async def execute_batch(
        self,
        connection: SSHConnection,
        commands: List[str],
        permissions: SubstationPermissions,
        operator: str,
        stop_on_error: bool = True,
        confirmed: bool = False
    ) -> List[OperationResponse]:
        """
        Execute multiple commands in sequence.

        Args:
            connection: SSH connection
            commands: List of commands
            permissions: Permission config
            operator: Operator username
            stop_on_error: Stop on first error
            confirmed: Skip confirmations

        Returns:
            List of responses
        """
        responses = []

        for command in commands:
            response = await self.execute(
                connection, command, permissions, operator, confirmed=confirmed
            )
            responses.append(response)

            if stop_on_error and not response.success:
                break

        return responses

    async def execute_script(
        self,
        connection: SSHConnection,
        script: str,
        permissions: SubstationPermissions,
        operator: str,
        interpreter: str = "/bin/bash",
        timeout: Optional[float] = None
    ) -> OperationResponse:
        """
        Execute a multi-line script.

        Args:
            connection: SSH connection
            script: Script content
            permissions: Permission config
            operator: Operator username
            interpreter: Script interpreter
            timeout: Execution timeout

        Returns:
            OperationResponse
        """
        # For scripts, we need write permission (scripts can do anything)
        if not permissions.write_enabled:
            request = self._create_request(f"[script: {len(script)} bytes]", connection.config.hostname, operator)
            return OperationResponse(
                request=request,
                result=None,
                success=False,
                error="Script execution requires write permission"
            )

        # Create a here-doc command
        command = f"{interpreter} << 'NIMSCRIPT'\n{script}\nNIMSCRIPT"

        return await self.execute(
            connection, command, permissions, operator, timeout=timeout, confirmed=True
        )


class DeploymentManager:
    """
    Manages agent deployment to remote substations.

    Handles:
    - File transfer
    - Environment setup
    - Process management
    - Health checks
    """

    def __init__(
        self,
        executor: Optional[RemoteExecutor] = None,
        audit_logger: Optional[AuditLogger] = None
    ):
        self.executor = executor or RemoteExecutor()
        self.audit = audit_logger or AuditLogger()

    async def deploy_agent(
        self,
        connection: SSHConnection,
        agent_id: str,
        agent_data: Dict,
        permissions: SubstationPermissions,
        operator: str,
        remote_path: str = "/opt/nim/agents"
    ) -> Dict:
        """
        Deploy an agent to a remote substation.

        Args:
            connection: SSH connection
            agent_id: Agent UUID
            agent_data: Agent configuration data
            permissions: Substation permissions
            operator: Operator username
            remote_path: Remote deployment path

        Returns:
            Deployment result dictionary
        """
        if not permissions.deploy_enabled:
            raise PermissionDenied("Deployment not enabled for this substation")

        deploy_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now(timezone.utc).isoformat()

        result = {
            "deployment_id": deploy_id,
            "agent_id": agent_id,
            "hostname": connection.config.hostname,
            "remote_path": remote_path,
            "deployed_at": timestamp,
            "deployed_by": operator,
            "status": "pending",
            "steps": []
        }

        try:
            # Step 1: Create directory
            mkdir_result = await self.executor.execute(
                connection,
                f"mkdir -p {remote_path}/{agent_id}",
                permissions,
                operator,
                confirmed=True
            )
            result["steps"].append({
                "step": "create_directory",
                "success": mkdir_result.success,
                "error": mkdir_result.error
            })

            if not mkdir_result.success:
                result["status"] = "failed"
                return result

            # Step 2: Write agent config
            config_json = json.dumps(agent_data, indent=2)
            config_cmd = f"cat > {remote_path}/{agent_id}/agent.json << 'AGENTCONFIG'\n{config_json}\nAGENTCONFIG"

            config_result = await self.executor.execute(
                connection, config_cmd, permissions, operator, confirmed=True
            )
            result["steps"].append({
                "step": "write_config",
                "success": config_result.success,
                "error": config_result.error
            })

            if not config_result.success:
                result["status"] = "failed"
                return result

            # Step 3: Set permissions
            chmod_result = await self.executor.execute(
                connection,
                f"chmod 600 {remote_path}/{agent_id}/agent.json",
                permissions,
                operator,
                confirmed=True
            )
            result["steps"].append({
                "step": "set_permissions",
                "success": chmod_result.success,
                "error": chmod_result.error
            })

            result["status"] = "deployed" if chmod_result.success else "failed"

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)

        return result

    async def check_deployment_health(
        self,
        connection: SSHConnection,
        agent_id: str,
        permissions: SubstationPermissions,
        operator: str,
        remote_path: str = "/opt/nim/agents"
    ) -> Dict:
        """
        Check health of a deployed agent.

        Returns deployment status and health information.
        """
        health = {
            "agent_id": agent_id,
            "hostname": connection.config.hostname,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "exists": False,
            "config_valid": False,
            "process_running": False,
            "details": {}
        }

        try:
            # Check if config exists
            check_result = await self.executor.execute(
                connection,
                f"test -f {remote_path}/{agent_id}/agent.json && echo 'exists'",
                permissions,
                operator
            )
            health["exists"] = "exists" in check_result.result.stdout if check_result.result else False

            if health["exists"]:
                # Validate config
                validate_result = await self.executor.execute(
                    connection,
                    f"python3 -c \"import json; json.load(open('{remote_path}/{agent_id}/agent.json'))\" 2>&1 || echo 'invalid'",
                    permissions,
                    operator
                )
                health["config_valid"] = "invalid" not in (validate_result.result.stdout if validate_result.result else "invalid")

                # Check for running process (if applicable)
                pid_result = await self.executor.execute(
                    connection,
                    f"cat {remote_path}/{agent_id}/.pid 2>/dev/null || echo ''",
                    permissions,
                    operator
                )
                if pid_result.result and pid_result.result.stdout.strip():
                    pid = pid_result.result.stdout.strip()
                    proc_result = await self.executor.execute(
                        connection,
                        f"ps -p {pid} > /dev/null 2>&1 && echo 'running'",
                        permissions,
                        operator
                    )
                    health["process_running"] = "running" in (proc_result.result.stdout if proc_result.result else "")
                    health["details"]["pid"] = pid

        except Exception as e:
            health["error"] = str(e)

        return health


# Module-level convenience
_default_executor: Optional[RemoteExecutor] = None


def get_executor() -> RemoteExecutor:
    """Get the default remote executor."""
    global _default_executor
    if _default_executor is None:
        _default_executor = RemoteExecutor()
    return _default_executor
