"""
Work-queue access log - who read which queue source, when.

The work queue is deliberately file-backed and therefore spread across
paths (project records, reference presets, spec documents). This module
makes every read observable in two forms:

- a JSONL audit trail at <logging.dir>/queue_access.jsonl with
  comprehensive detail (operation, project, path, priority, operator,
  pid, timestamp), appended on every queue-source access;
- a stdout ceremony for interactive processing:

      Processing Work Queue: PROJECT nim-agency-dev [pri 3] (data/agency/data/projects/<id>.json)

Observability must never break the operation - logging failures are
swallowed, ceremonies are plain prints.
"""

import getpass
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import PROJECT_ROOT, log_dir

QUEUE_LOG_NAME = "queue_access.jsonl"


def queue_log_path() -> Path:
    return log_dir() / QUEUE_LOG_NAME


def repo_relative(path) -> str:
    """Render a queue path repo-relative when possible (friendlier)."""
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except (ValueError, OSError):
        return str(path)


def project_record_path(ds, project: dict) -> Path:
    """The on-disk record backing a project aggregate."""
    config = ds.ENTITY_CONFIG["project"]
    return ds.base_path / config["dir"] / f"{project['id']}.json"


def log_queue_access(
    operation: str,
    project: Optional[dict] = None,
    path=None,
    detail: str = "",
) -> None:
    """Append one access record to the queue audit trail."""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "operator": getpass.getuser(),
        "pid": os.getpid(),
    }
    if project is not None:
        entry["project"] = project.get("slug")
        entry["project_id"] = project.get("id")
        entry["priority"] = project.get("priority")
    if path is not None:
        entry["path"] = repo_relative(path)
    if detail:
        entry["detail"] = detail
    try:
        target = queue_log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a") as f:
            f.write(json.dumps(entry) + "\n")
        if os.name != "nt":
            os.chmod(target, 0o600)
    except OSError:
        pass


def queue_banner(project: dict, path) -> str:
    """The stdout ceremony line for interactive queue processing."""
    return (f"Processing Work Queue: PROJECT {project.get('slug', '?')} "
            f"[pri {project.get('priority', '?')}] ({repo_relative(path)})")


def source_line(label: str, path) -> str:
    """Ceremony for a secondary queue source (spec, preset, reference)."""
    return f"  reading queue source: {label} ({repo_relative(path)})"
