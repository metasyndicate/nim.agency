"""The schema catalog (schema/index.json, a dcat:Catalog), the schema files,
and the datastore's storage config describe one model. These invariants
keep the three from drifting apart again (they had: 9 schemas unlisted,
4 duplicate nim.ids, 3 data paths that did not exist)."""

import json
import unittest
from pathlib import Path

from tests.support import REPO

from lib.data import refs
from lib.data.crud import DataStore

SCHEMA = REPO / "data/agency/schema"
INDEX = json.loads((SCHEMA / "index.json").read_text())
ENTRIES = {name: entry for section in INDEX["schemas"].values()
           for name, entry in section.items()}


def schema_files():
    return sorted(p for p in SCHEMA.glob("*/*.json")
                  if p.name.endswith((".schema.json", ".metaschema.json")))


def storage_path(config: dict) -> str:
    """The catalog's data_path spelling of a datastore storage config."""
    root = "~/.nim/agency/" if config.get("local_storage") else "../data/"
    kind = config["type"]
    if kind == "reference":
        return f"../data/reference/{config['file']}"
    if kind == "singleton":
        return f"../data/reference/{config['file']}"
    if kind == "collection":
        return f"{root}{config['dir']}/"
    return f"{root}{config['file']}"


class TestCatalog(unittest.TestCase):

    def test_every_schema_file_is_catalogued_and_every_entry_exists(self):
        catalogued = {e["path"] for e in ENTRIES.values()}
        on_disk = {str(p.relative_to(SCHEMA)) for p in schema_files()}
        self.assertEqual(sorted(on_disk - catalogued), [])
        self.assertEqual(sorted(catalogued - on_disk), [])

    def test_nim_ids_are_unique_and_match_the_catalog(self):
        seen = {}
        for path in schema_files():
            nim_id = json.loads(path.read_text())["$nim.meta"]["nim.id"]
            self.assertNotIn(nim_id, seen, f"{path.name} and {seen.get(nim_id)}")
            seen[nim_id] = path.name
        for name, entry in ENTRIES.items():
            schema = json.loads((SCHEMA / entry["path"]).read_text())
            self.assertEqual(entry["nim_id"], schema["$nim.meta"]["nim.id"], name)

    def test_catalogued_reference_data_exists(self):
        # Reference data ships with the repo; logs and collections are
        # created on first write (their paths are checked against storage
        # config below)
        for name, entry in ENTRIES.items():
            path = entry.get("data_path", "")
            if path.startswith("../data/reference/"):
                self.assertTrue((SCHEMA / path).resolve().exists(),
                                f"{name}: {path}")

    def test_every_stored_entity_is_catalogued_where_it_is_stored(self):
        ds = DataStore()
        for entity, config in ds.ENTITY_CONFIG.items():
            name = refs.schema_name(ds, entity)
            self.assertIn(name, ENTRIES, f"{entity} has no catalogued schema")
            self.assertEqual(ENTRIES[name].get("data_path"),
                             storage_path(config), entity)


class TestOntologyAnnotations(unittest.TestCase):
    """@type and `rdf:property` comments must use prefixes the metaschema's
    @context declares, or they name nothing."""

    def test_every_prefix_used_is_declared(self):
        import re
        meta = json.loads((SCHEMA / "meta/agency.metaschema.json").read_text())
        declared = set(meta["@context"])
        for path in schema_files() + [SCHEMA / "index.json"]:
            text = path.read_text()
            for value in re.findall(
                    r'"(?:@type|\$comment)": ("[^"]*"|\[[^\]]*\])', text):
                for prefix in re.findall(r"\b([a-z][a-z0-9-]*):[A-Za-z]", value):
                    if prefix not in ("http", "https"):
                        self.assertIn(prefix, declared, f"{path.name}: {value}")

    def test_reference_row_schemas_describe_concepts(self):
        # A reference table is a skos:ConceptScheme; each row its schema
        # validates is a skos:Concept
        for path in (SCHEMA / "reference").glob("*.schema.json"):
            types = json.loads(path.read_text()).get("@type", [])
            types = [types] if isinstance(types, str) else types
            self.assertNotIn("skos:ConceptScheme", types, path.name)


if __name__ == "__main__":
    unittest.main()
