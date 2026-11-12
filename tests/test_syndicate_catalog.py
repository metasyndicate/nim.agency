"""Syndicate vocabulary (plan.101526.syndicate.md V0): syndicate kinds, auth
methods, the vendor catalog, and harnesses. Every reference resolves, the
kind rules hold, and the catalog names secrets only by variable name."""

import unittest

from tests.support import DatastoreCase

from lib.data import refs
from lib.data.schema import get_schema_manager

ENTITIES = ["syndicate_kind", "auth_method", "vendor", "harness"]


class TestCatalog(DatastoreCase):

    def rows(self, entity) -> dict:
        return {r["slug"]: r for r in self.ds.load_reference(entity)}

    def test_every_reference_resolves(self):
        report = refs.check(self.ds, get_schema_manager(), ENTITIES)
        self.assertGreater(report.checked, 0)
        self.assertEqual([str(f) for f in report.findings], [])

    def test_the_three_kinds(self):
        kinds = self.rows("syndicate_kind")
        self.assertEqual(sorted(kinds), ["gateway", "merchant", "rogue"])
        for slug, kind in kinds.items():
            self.assertEqual(kind["display"], f"{slug} syndicate")
        self.assertEqual(kinds["rogue"]["control_plane"], "full")
        self.assertEqual(kinds["merchant"]["harness_owner"], "vendor")
        self.assertEqual(kinds["gateway"]["harness_owner"], "agency")

    def test_every_merchant_vendor_ships_a_harness(self):
        harnesses = self.rows("harness").values()
        for slug, vendor in self.rows("vendor").items():
            if vendor["kind_slug"] == "merchant":
                self.assertTrue(any(h["vendor_slug"] == slug for h in harnesses), slug)

    def test_the_agency_harness_has_no_vendor(self):
        self.assertIsNone(self.rows("harness")["nim"]["vendor_slug"])

    def test_api_key_vendors_name_their_variables_and_only_names(self):
        for slug, vendor in self.rows("vendor").items():
            names = vendor.get("api_key_env", [])
            self.assertEqual(bool(names), "api_key" in vendor["auth_method_slugs"], slug)
            for name in names:
                self.assertRegex(name, r"^[A-Z][A-Z0-9_]*$")

    def test_secrets_never_live_in_the_datastore(self):
        places = {r["secret"] for r in self.ds.load_reference("auth_method")}
        self.assertLessEqual(places, {"none", "environment", "harness_session", "vault"})

    def test_unverified_rows_say_so(self):
        for entity in ("vendor", "harness"):
            for slug, row in self.rows(entity).items():
                self.assertIn("verified", row, f"{entity}.{slug}")
                if row["verified"] is not None:
                    self.assertTrue(row["verified"]["sources"], f"{entity}.{slug}")


if __name__ == "__main__":
    unittest.main()
