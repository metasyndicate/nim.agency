"""Host facts for platform records, and the subprocess/HTTP seams the
capturers use (injectable, so tests never touch the real host)."""

import json
import platform as _platform
import shutil
import socket
import subprocess
import urllib.request
from typing import Optional


def run(argv: list[str], timeout: float = 15.0) -> Optional[str]:
    """stdout of a command, or None when it is missing or fails."""
    if not shutil.which(argv[0]):
        return None
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def fetch(url: str, payload: Optional[dict] = None,
          timeout: float = 10.0) -> Optional[dict]:
    """Decoded JSON from a local HTTP API, or None when unreachable."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except (OSError, ValueError):
        return None


def tailnet_name(runner=run) -> Optional[str]:
    out = runner(["tailscale", "status", "--json"])
    if not out:
        return None
    try:
        name = (json.loads(out).get("Self") or {}).get("DNSName") or ""
    except ValueError:
        return None
    return name.rstrip(".") or None


def tailscale_proxies(port: int, runner=run) -> list[dict]:
    """tailscale serve/funnel handlers that proxy to this host's `port`."""
    out = runner(["tailscale", "serve", "status", "--json"])
    if not out:
        return []
    try:
        config = json.loads(out)
    except ValueError:
        return []
    proxies = []
    blocks = [("", config)] + list((config.get("Services") or {}).items())
    for name, block in blocks:
        for hostport, web in (block.get("Web") or {}).items():
            for mount, handler in (web.get("Handlers") or {}).items():
                target = handler.get("Proxy") or ""
                if target.rstrip("/").endswith(f":{port}"):
                    host = hostport.removesuffix(":443")
                    funnel = (config.get("AllowFunnel") or {}).get(hostport)
                    proxy = {"kind": "tailscale_funnel" if funnel else "tailscale_serve",
                             "url": f"https://{host}{mount if mount != '/' else ''}",
                             "target": target,
                             "scope": "public" if funnel else "tailnet"}
                    if name:
                        proxy["name"] = name
                    proxies.append(proxy)
    return proxies


def facts(host_name: Optional[str] = None, runner=run) -> dict:
    """What the record says about the machine itself."""
    hostname = socket.gethostname()
    facts = {
        "name": host_name or hostname,
        "hostname": hostname,
        "tailnet_name": tailnet_name(runner),
        "os": _platform.system().lower(),
        "os_version": _platform.mac_ver()[0] or _platform.release(),
        "arch": _platform.machine(),
    }
    cpu = runner(["sysctl", "-n", "machdep.cpu.brand_string"])
    if cpu:
        facts["cpu"] = cpu.strip()
    memory = runner(["sysctl", "-n", "hw.memsize"])
    if memory and memory.strip().isdigit():
        facts["memory_bytes"] = int(memory.strip())
    return facts


ALF = "/usr/libexec/ApplicationFirewall/socketfilterfw"


def alf_blocked(binary: str, runner=run) -> Optional[bool]:
    """Whether the macOS Application Firewall refuses incoming connections
    to `binary` (None when there is no ALF or it cannot say). Readable
    without root; changing it needs root."""
    if not shutil.which(ALF) and runner is run:
        return None
    out = runner([ALF, "--getappblocked", binary])
    if out is None:
        return None
    if "is blocked" in out:
        return True
    if "is permitted" in out or "not part of the firewall" in out:
        return False
    return None


def alf_block_commands(binary: str) -> list[str]:
    return [f"sudo {ALF} --add {binary}", f"sudo {ALF} --blockapp {binary}"]
