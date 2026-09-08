"""Shared fixtures: an isolated datastore with real reference data.

Every test runs against a temp copy of data/agency/data/reference so
nothing touches the working datastore. Run the suite with:

    python3 -m unittest discover -s tests -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.data.crud import DataStore  # noqa: E402


class DatastoreCase(unittest.TestCase):
    """Base case with an isolated DataStore per test class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nim-tests-"))
        shutil.copytree(REPO / "data/agency/data/reference",
                        cls.tmp / "reference")
        # The syndicate inventory travels with the reference data: the
        # capability gate is only meaningful against real channel records.
        syndicates = REPO / "data/agency/data/syndicates"
        if syndicates.exists():
            shutil.copytree(syndicates, cls.tmp / "syndicates")
        cls.ds = DataStore(base_path=str(cls.tmp))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


def fake_agent(n: int, codename: str, **extra) -> dict:
    """Minimal agent dict for relation-level tests (no generator needed)."""
    agent = {
        "id": f"{n:08d}-0000-4000-8000-{n:012d}",
        "identity": {"name": f"Agt. Test Unit{n}", "codename": codename,
                     "role_title": "Field Specialist",
                     "dogtag": f"agt.test.unit{n}"},
        "economy": {"gold": 0, "xp": 0},
        "classification": {},
    }
    agent.update(extra)
    return agent
