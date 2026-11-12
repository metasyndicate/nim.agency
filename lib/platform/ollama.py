"""Ollama capturer: reads a host's Ollama installation into a platform
record - the unit that runs `ollama serve`, its OLLAMA_* settings, what
fronts it, and every pulled model pinned by digest."""

import plistlib
import shlex
from pathlib import Path
from typing import Optional

from . import host as hostlib

DEFAULT_BIND = "127.0.0.1:11434"
# Ollama is configured entirely through OLLAMA_* variables and none of them
# carries a secret; anything else in a unit's environment stays off record.
ENV_PREFIX = "OLLAMA_"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def normalize_bind(value: Optional[str]) -> str:
    """OLLAMA_HOST as host:port (it accepts a scheme and a bare host)."""
    value = (value or DEFAULT_BIND).strip()
    value = value.split("://", 1)[-1].rstrip("/")
    if ":" not in value:
        value = f"{value}:11434"
    return value


def api_base(bind: str) -> str:
    host, port = bind.rsplit(":", 1)
    if host in ("0.0.0.0", "", "::", "[::]"):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def find_unit(agents_dir: Path = AGENTS_DIR,
              prefer: Optional[str] = None) -> Optional[dict]:
    """The launchd unit that runs `ollama serve` (the preferred label
    first, else any agent whose program is ollama)."""
    units = []
    for path in sorted(Path(agents_dir).glob("*.plist")):
        try:
            with open(path, "rb") as f:
                unit = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        argv = unit.get("ProgramArguments") or []
        if argv and Path(argv[0]).name == "ollama" and "serve" in argv[1:]:
            units.append(unit)
    for unit in units:
        if unit.get("Label") == prefer:
            return unit
    return units[0] if units else None


def parse_parameters(text: str) -> dict:
    """Modelfile PARAMETER lines as returned by /api/show `parameters`
    (repeated keys such as `stop` become lists)."""
    params: dict = {}
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, raw = parts
        try:
            value = shlex.split(raw)[0] if raw.startswith('"') else raw.strip()
        except ValueError:
            value = raw.strip()
        for cast in (int, float):
            try:
                value = cast(value)
                break
            except (TypeError, ValueError):
                continue
        if key in params:
            existing = params[key]
            params[key] = (existing if isinstance(existing, list)
                           else [existing]) + [value]
        else:
            params[key] = value
    return params


def artifacts(base: str, fetch=hostlib.fetch) -> list[dict]:
    tags = fetch(f"{base}/api/tags") or {}
    found = []
    for model in tags.get("models") or []:
        details = model.get("details") or {}
        entry = {"kind": "model", "name": model["name"],
                 "digest": model.get("digest", ""),
                 "size_bytes": int(model.get("size") or 0),
                 "source": f"ollama.com/library/{model['name']}"}
        for src, dst in (("family", "family"), ("parameter_size", "parameter_size"),
                         ("quantization_level", "quantization")):
            if details.get(src):
                entry[dst] = details[src]
        if details.get("context_length"):
            entry["context_length"] = int(details["context_length"])
        if model.get("capabilities"):
            entry["capabilities"] = list(model["capabilities"])
        shown = fetch(f"{base}/api/show", {"model": model["name"]}) or {}
        if shown.get("parameters"):
            entry["parameters"] = parse_parameters(shown["parameters"])
        found.append(entry)
    return found


def served(base: str, fetch=hostlib.fetch) -> dict:
    """What the server is actually doing: loaded models and the context
    window it allocated for each (ollama ps)."""
    ps = fetch(f"{base}/api/ps") or {}
    loaded = {}
    for model in ps.get("models") or []:
        loaded[model["name"]] = {k: model[k] for k in
                                 ("context_length", "size_vram")
                                 if k in model}
    return {"loaded": loaded}


def capture(slug: str, host_name: Optional[str] = None,
            runner=hostlib.run, fetch=hostlib.fetch,
            agents_dir: Path = AGENTS_DIR, prefer_label: Optional[str] = None
            ) -> dict:
    unit = find_unit(agents_dir, prefer=prefer_label)
    if unit is None:
        program = [runner(["which", "ollama"]) or "ollama", "serve"]
        program[0] = program[0].strip()
        service = {"manager": "manual", "label": "", "program": program,
                   "environment": {}}
        env = {}
    else:
        env = {k: str(v) for k, v in (unit.get("EnvironmentVariables") or {}).items()
               if k.startswith(ENV_PREFIX)}
        service = {
            "manager": "launchd", "label": unit.get("Label", ""),
            "scope": "user", "program": list(unit["ProgramArguments"]),
            "environment": env,
            "keep_alive": bool(unit.get("KeepAlive", False)),
            "run_at_load": bool(unit.get("RunAtLoad", False)),
        }
        for src, dst in (("WorkingDirectory", "working_directory"),
                         ("StandardOutPath", "stdout_path"),
                         ("StandardErrorPath", "stderr_path")):
            if unit.get(src):
                service[dst] = unit[src]

    bind = normalize_bind(env.get("OLLAMA_HOST"))
    base = api_base(bind)
    version = (fetch(f"{base}/api/version") or {}).get("version")
    if not version:
        out = runner([service["program"][0], "--version"]) or ""
        version = out.strip().rsplit(" ", 1)[-1] or "unknown"
    binary = service["program"][0]
    method = "homebrew" if "/homebrew/" in binary or "/Cellar/" in binary else "binary"
    software = {"version": version,
                "install": {"method": method, "binary": binary}}
    if method == "homebrew":
        software["install"]["package"] = "ollama"

    port = int(bind.rsplit(":", 1)[1])
    exposure = {"bind": bind, "proxies": hostlib.tailscale_proxies(port, runner)}
    # The process launchd runs is the resolved binary, not the opt/ symlink;
    # firewall rules attach to that path (and move with each upgrade)
    resolved = str(Path(binary).resolve())
    if hostlib.alf_blocked(resolved, runner):
        exposure["guards"] = [{"kind": "alf_block_incoming", "target": resolved}]
    return {
        "slug": slug,
        "label": f"Ollama on {host_name or hostlib.socket.gethostname()}",
        "host": hostlib.facts(host_name, runner),
        "software": software,
        "service": service,
        "exposure": exposure,
        "artifacts": artifacts(base, fetch),
        "served": served(base, fetch),
    }
