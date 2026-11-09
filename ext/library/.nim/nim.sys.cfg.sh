#!/usr/bin/env bash

# ⎰𝙉𝙄𝙈⎰  METASHELL SUBSYSTEM MANIFEST
# ----------------------------------------------------------------------------
# Sourced by nim.mod.register from <subsystem>/.nim/nim.sys.cfg.sh.
# Declarations only — no runtime logic, no side effects on source.
# ----------------------------------------------------------------------------

# canonical subsystem identity
NIM_SYS_NAME="library"
NIM_SYS_VERSION="0.1.0"

# subsystem deps — bare module names; resolved through _NIM_LIBPATHS by nim.source
NIM_SYS_DEPS=( core fn log output validate )

# modules in this subsystem — sourced by nim.mod.register after deps resolve
NIM_SYS_MODS=( library )

# subsystem root path; modules live directly under this dir (no lib/ subdir)
NIM_SYS_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
