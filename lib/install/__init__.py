"""
Installer subsystem - instance paths, environment checks, virtual
environments, and install records. Stdlib only, so it runs before any
optional dependency exists. Plan of record: .nim/work/plan.101426.installer.md
"""

from .paths import (
    APP, COMPONENT, DEFAULT_INSTANCE, REPO_ROOT, Resolved, home, platform_dirs,
    probe, resolve_home, resolve_venv, venv_python,
)

__all__ = [
    "APP", "COMPONENT", "DEFAULT_INSTANCE", "REPO_ROOT", "Resolved", "home",
    "platform_dirs", "probe", "resolve_home", "resolve_venv", "venv_python",
]
