"""Suite-wide isolation: every test runs against a throwaway instance home.

Without this, anything that logs or records through lib.install.home()
(queue access audit, operator-local records) writes into the operator's
real home - or wherever NIM_HOME happens to point, such as a metashell
checkout. Tests that exercise home resolution patch NIM_HOME themselves.
"""

import atexit
import os
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nim-tests-home-")
os.environ["NIM_HOME"] = _HOME
atexit.register(shutil.rmtree, _HOME, True)

from lib.install import paths as _paths  # noqa: E402

_paths.reset()
