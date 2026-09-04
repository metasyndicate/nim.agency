"""
Command Safety - Classify commands as read-safe or write-dangerous

Provides command classification to enforce permission controls
for remote execution.
"""

import re
import shlex
from enum import Enum
from dataclasses import dataclass
from typing import List, Set, Optional, Tuple


class OperationIntent(Enum):
    """Classification of command intent."""
    READ = "read"      # Safe read operations
    WRITE = "write"    # Modifying operations
    DEPLOY = "deploy"  # Deployment operations
    UNKNOWN = "unknown"


@dataclass
class CommandClassification:
    """Result of command classification."""
    command: str
    intent: OperationIntent
    confidence: float  # 0.0 to 1.0
    reasons: List[str]
    dangerous_patterns: List[str]

    @property
    def is_safe(self) -> bool:
        return self.intent == OperationIntent.READ

    @property
    def requires_permission(self) -> bool:
        return self.intent in (OperationIntent.WRITE, OperationIntent.DEPLOY)


class CommandClassifier:
    """
    Classifies shell commands as read-safe or write-dangerous.

    Uses pattern matching to identify potentially dangerous operations.
    Conservative by default - unknown commands are marked as write.

    Usage:
        classifier = CommandClassifier()
        result = classifier.classify("ls -la /tmp")
        if result.is_safe:
            # Execute without confirmation
        else:
            # Require write permission or confirmation
    """

    # Commands that are always safe (read-only)
    READ_SAFE_COMMANDS: Set[str] = {
        # File inspection
        "ls", "ll", "la", "dir", "tree",
        "cat", "head", "tail", "less", "more", "bat",
        "file", "stat", "wc", "md5sum", "sha256sum",
        "find", "locate", "which", "whereis", "type",
        "readlink", "realpath",

        # Text processing (read-only)
        "grep", "egrep", "fgrep", "rg", "ag", "ack",
        "sed", "awk", "cut", "sort", "uniq", "tr", "column",
        "diff", "cmp", "comm",
        "jq", "yq", "xq",

        # System inspection
        "ps", "top", "htop", "atop", "btop",
        "free", "vmstat", "iostat", "mpstat",
        "df", "du", "lsblk", "mount", "findmnt",
        "uptime", "who", "w", "last", "lastlog",
        "uname", "hostname", "hostnamectl",
        "id", "whoami", "groups",
        "env", "printenv", "set",
        "date", "cal", "timedatectl",
        "dmesg", "journalctl",
        "lscpu", "lsmem", "lspci", "lsusb", "lshw",
        "dmidecode", "hwinfo",

        # Network inspection
        "ip", "ifconfig", "netstat", "ss",
        "ping", "ping6", "traceroute", "tracepath", "mtr",
        "dig", "nslookup", "host", "whois",
        "curl", "wget",  # Can be dangerous with -o, handled separately
        "nc", "telnet",  # Read in most cases
        "arp", "route",

        # Process inspection
        "pgrep", "pidof", "pstree",
        "lsof", "fuser",

        # Package inspection (not install)
        "rpm", "dpkg", "apt-cache", "dnf", "yum",
        "pip", "pip3", "npm", "cargo", "gem", "go",  # list/show only

        # Container inspection
        "docker", "podman",  # inspect/ps only
        "kubectl",  # get/describe only

        # Module system
        "module", "ml",

        # Version/help
        "help", "man", "info", "apropos",

        # Shell builtins
        "echo", "printf", "test", "[", "[[",
        "true", "false", "pwd", "cd",
    }

    # Commands that are always write/dangerous
    WRITE_DANGEROUS_COMMANDS: Set[str] = {
        # File modification
        "rm", "rmdir", "unlink",
        "mv", "rename",
        "cp",  # Creates files
        "mkdir", "mktemp",
        "touch", "truncate",
        "chmod", "chown", "chgrp", "chattr",
        "ln", "link", "symlink",
        "shred", "srm",

        # Editors (could modify)
        "vi", "vim", "nvim", "nano", "emacs", "ed",

        # System modification
        "reboot", "shutdown", "poweroff", "halt", "init",
        "systemctl", "service", "update-rc.d", "chkconfig",
        "mount", "umount",
        "mkfs", "fdisk", "parted", "gdisk",
        "dd", "sync",

        # Package management (install/remove)
        "apt", "apt-get", "aptitude",
        "yum", "dnf", "zypper",
        "pacman", "emerge",
        "snap", "flatpak",

        # User management
        "useradd", "userdel", "usermod",
        "groupadd", "groupdel", "groupmod",
        "passwd", "chpasswd",

        # Network modification
        "iptables", "ip6tables", "nft", "firewall-cmd",
        "nmcli", "nmtui",

        # Container management
        "docker-compose", "kubectl",

        # Dangerous utilities
        "crontab",
        "at", "batch",
        "kill", "killall", "pkill",
        "nohup", "disown",
    }

    # Patterns that indicate write operations
    WRITE_PATTERNS: List[Tuple[re.Pattern, str]] = [
        # Output redirection
        (re.compile(r'[^<]>\s*\S'), "Output redirection (>)"),
        (re.compile(r'>>\s*\S'), "Append redirection (>>)"),
        (re.compile(r'\|\s*tee\s'), "Pipe to tee"),

        # Dangerous subcommands
        (re.compile(r'\bapt(-get)?\s+(install|remove|purge|upgrade)'), "APT install/remove"),
        (re.compile(r'\b(yum|dnf)\s+(install|remove|erase|upgrade)'), "YUM/DNF install/remove"),
        (re.compile(r'\bpip3?\s+install'), "Pip install"),
        (re.compile(r'\bnpm\s+(install|uninstall|update)'), "NPM install"),
        (re.compile(r'\bcargo\s+(install|uninstall)'), "Cargo install"),

        # Docker/container write operations
        (re.compile(r'\bdocker\s+(run|start|stop|kill|rm|rmi|pull|push|build)'), "Docker write operation"),
        (re.compile(r'\bpodman\s+(run|start|stop|kill|rm|rmi|pull|push|build)'), "Podman write operation"),
        (re.compile(r'\bkubectl\s+(apply|create|delete|edit|patch|replace|scale)'), "Kubectl write operation"),

        # Service control
        (re.compile(r'\bsystemctl\s+(start|stop|restart|enable|disable|reload|mask)'), "Systemctl control"),
        (re.compile(r'\bservice\s+\S+\s+(start|stop|restart|reload)'), "Service control"),

        # Git write operations
        (re.compile(r'\bgit\s+(push|commit|reset|revert|merge|rebase|checkout|branch\s+-d)'), "Git write operation"),

        # Process control
        (re.compile(r'\bkill\s+-'), "Kill signal"),
        (re.compile(r'\bpkill\b'), "Process kill"),
        (re.compile(r'\bkillall\b'), "Kill all"),

        # Sudo/su (escalation)
        (re.compile(r'\bsudo\b'), "Sudo execution"),
        (re.compile(r'\bsu\s+-'), "Switch user"),

        # Dangerous flags
        (re.compile(r'\brm\s+.*-r'), "Recursive remove"),
        (re.compile(r'\brm\s+.*-f'), "Force remove"),
        (re.compile(r'\bchmod\s+.*777'), "World-writable chmod"),
        (re.compile(r'\bdd\s+'), "dd command"),

        # Curl/wget with output
        (re.compile(r'\bcurl\s+.*-[oO]'), "Curl with output file"),
        (re.compile(r'\bwget\s+(?!.*--spider)'), "Wget download"),
    ]

    # Patterns that indicate read-safe operations
    READ_PATTERNS: List[Tuple[re.Pattern, str]] = [
        # Docker/container read operations
        (re.compile(r'\bdocker\s+(ps|images|inspect|logs|stats|top|port|version|info)'), "Docker read operation"),
        (re.compile(r'\bpodman\s+(ps|images|inspect|logs|stats|top|port|version|info)'), "Podman read operation"),
        (re.compile(r'\bkubectl\s+(get|describe|logs|explain|api-resources|version)'), "Kubectl read operation"),

        # Git read operations
        (re.compile(r'\bgit\s+(status|log|diff|show|branch|remote|tag|stash\s+list)'), "Git read operation"),

        # Package query operations
        (re.compile(r'\bapt(-cache)?\s+(show|search|policy|depends)'), "APT query"),
        (re.compile(r'\b(yum|dnf)\s+(list|search|info|deplist|repolist)'), "YUM/DNF query"),
        (re.compile(r'\bpip3?\s+(list|show|search|freeze)'), "Pip query"),
        (re.compile(r'\bnpm\s+(list|ls|outdated|view|search)'), "NPM query"),

        # Systemctl status
        (re.compile(r'\bsystemctl\s+(status|is-active|is-enabled|list-units|list-timers)'), "Systemctl status"),

        # Curl without output
        (re.compile(r'\bcurl\s+(?!.*-[oO])'), "Curl read-only"),
        (re.compile(r'\bwget\s+.*--spider'), "Wget spider mode"),
    ]

    def __init__(self, strict: bool = True):
        """
        Initialize classifier.

        Args:
            strict: If True, unknown commands are marked as WRITE.
                   If False, unknown commands are marked as UNKNOWN.
        """
        self.strict = strict

    def classify(self, command: str) -> CommandClassification:
        """
        Classify a command's intent.

        Args:
            command: Shell command string

        Returns:
            CommandClassification with intent and reasoning
        """
        command = command.strip()
        reasons = []
        dangerous_patterns = []

        if not command:
            return CommandClassification(
                command=command,
                intent=OperationIntent.READ,
                confidence=1.0,
                reasons=["Empty command"],
                dangerous_patterns=[]
            )

        # Parse command to get base command
        try:
            parts = shlex.split(command)
            base_cmd = parts[0].split("/")[-1] if parts else ""
        except ValueError:
            # Unparseable command - treat as dangerous
            return CommandClassification(
                command=command,
                intent=OperationIntent.WRITE,
                confidence=0.7,
                reasons=["Unparseable command syntax"],
                dangerous_patterns=[]
            )

        # Check for explicit read patterns first
        for pattern, description in self.READ_PATTERNS:
            if pattern.search(command):
                reasons.append(f"Matched read pattern: {description}")

        # Check for write patterns
        for pattern, description in self.WRITE_PATTERNS:
            if pattern.search(command):
                dangerous_patterns.append(description)
                reasons.append(f"Matched write pattern: {description}")

        # Check base command against known lists
        if base_cmd in self.WRITE_DANGEROUS_COMMANDS:
            reasons.append(f"Base command '{base_cmd}' is in dangerous list")
            if not dangerous_patterns:
                dangerous_patterns.append(f"Dangerous command: {base_cmd}")

        is_safe_cmd = base_cmd in self.READ_SAFE_COMMANDS

        # Determine final classification
        if dangerous_patterns:
            return CommandClassification(
                command=command,
                intent=OperationIntent.WRITE,
                confidence=0.9 if len(dangerous_patterns) > 1 else 0.8,
                reasons=reasons,
                dangerous_patterns=dangerous_patterns
            )

        if is_safe_cmd and not dangerous_patterns:
            # Check for output redirection even on safe commands
            if re.search(r'[^<]>\s*\S', command) or re.search(r'>>\s*\S', command):
                return CommandClassification(
                    command=command,
                    intent=OperationIntent.WRITE,
                    confidence=0.85,
                    reasons=reasons + ["Safe command with output redirection"],
                    dangerous_patterns=["Output redirection"]
                )

            return CommandClassification(
                command=command,
                intent=OperationIntent.READ,
                confidence=0.95,
                reasons=reasons + [f"Base command '{base_cmd}' is read-safe"],
                dangerous_patterns=[]
            )

        # Unknown command
        if self.strict:
            return CommandClassification(
                command=command,
                intent=OperationIntent.WRITE,
                confidence=0.5,
                reasons=reasons + [f"Unknown command '{base_cmd}' treated as write (strict mode)"],
                dangerous_patterns=[f"Unknown command: {base_cmd}"]
            )
        else:
            return CommandClassification(
                command=command,
                intent=OperationIntent.UNKNOWN,
                confidence=0.5,
                reasons=reasons + [f"Unknown command '{base_cmd}'"],
                dangerous_patterns=[]
            )

    def classify_batch(self, commands: List[str]) -> List[CommandClassification]:
        """Classify multiple commands."""
        return [self.classify(cmd) for cmd in commands]

    def is_safe(self, command: str) -> bool:
        """Quick check if command is read-safe."""
        return self.classify(command).is_safe

    def requires_permission(self, command: str) -> bool:
        """Quick check if command requires write permission."""
        return self.classify(command).requires_permission


# Module-level convenience
_default_classifier: Optional[CommandClassifier] = None


def get_classifier(strict: bool = True) -> CommandClassifier:
    """Get the default command classifier."""
    global _default_classifier
    if _default_classifier is None or _default_classifier.strict != strict:
        _default_classifier = CommandClassifier(strict=strict)
    return _default_classifier


def classify_command(command: str) -> CommandClassification:
    """Convenience function to classify a command."""
    return get_classifier().classify(command)


def is_safe_command(command: str) -> bool:
    """Convenience function to check if command is safe."""
    return get_classifier().is_safe(command)
