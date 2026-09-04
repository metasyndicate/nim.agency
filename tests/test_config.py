"""Global dispatch modality: posture semantics and the clamp."""

import unittest

from tests.support import REPO  # noqa: F401  (sys.path side effect)

from lib.config import get_config, modality, modality_violation


class TestModality(unittest.TestCase):
    """Mutates the cached config in-process; restores in tearDown."""

    def setUp(self):
        self.cfg = get_config()
        self.original = self.cfg["dispatch"].get("modality", "readonly")

    def tearDown(self):
        self.cfg["dispatch"]["modality"] = self.original

    def _set(self, value):
        self.cfg["dispatch"]["modality"] = value

    def test_default_posture_is_readonly(self):
        self._set("readonly")
        self.assertEqual(modality(), "readonly")
        self.assertIsNone(modality_violation(True, True))
        self.assertIsNotNone(modality_violation(False, True))
        self.assertIsNotNone(modality_violation(True, False))

    def test_supervised_allows_local_writes_only(self):
        self._set("supervised")
        self.assertIsNone(modality_violation(False, True))
        self.assertIsNotNone(modality_violation(True, False))

    def test_unrestricted_allows_remote(self):
        self._set("unrestricted")
        self.assertIsNone(modality_violation(True, False))

    def test_unknown_posture_clamps_to_readonly(self):
        self._set("bogus")
        self.assertEqual(modality(), "readonly")
        self.assertIsNotNone(modality_violation(False, True))


if __name__ == "__main__":
    unittest.main()
