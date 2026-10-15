"""Persona layers (plan .nim/work/plan.101426.persona.md): role grounding
from O*NET / NICE work roles, work styles, tone axes, flavor kept out of
the prompt, the v1 -> v2 migration, and the derived temperament."""

import json
import random
import unittest

from tests.support import REPO, DatastoreCase

from lib.data import lex
from lib.data.composer import AgentComposer
from lib.data.generator import AgentGenerator
from lib.data.persona import Persona

REF = REPO / "data/agency/data/reference"


def agent(personality: dict) -> dict:
    return {"id": "t", "identity": {"name": "Agt. Test", "codename": "t.t",
                                    "role_title": "Tester"},
            "classification": {"rank_id": 4, "qualifier_id": 3}, "economy": {"xp": 1000},
            "profile": {"personality": personality}}


class TestGrounding(DatastoreCase):

    def setUp(self):
        self.persona = Persona(self.ds)
        self.archetypes = {a["slug"]: a for a in self.ds.load_reference("archetype")}

    def test_technical_archetypes_lead_with_an_occupation(self):
        lore = {"cat", "owl", "queen", "forager", "drone"}
        for slug, arch in self.archetypes.items():
            g = self.persona.grounding(arch)
            if slug in lore:
                self.assertIsNone(g.occupation, slug)
                continue
            self.assertIsNotNone(g.occupation, slug)
            self.assertEqual(arch["work_role_slugs"][0], g.occupation["slug"], slug)

    def test_core_tasks_by_importance_and_styles_by_impact(self):
        g = self.persona.grounding(self.archetypes["linux_admin"])
        role = g.occupation
        texts = {t["slug"]: t["description"] for t in self.ds.load_reference("work_task")}
        core = sorted((t for t in role["tasks"] if t["type"] == "Core"),
                      key=lambda t: -t["importance"])
        self.assertEqual(g.core_tasks, [texts[t["task"]] for t in core[:len(g.core_tasks)]])
        impacts = {s["style"]: s["impact"] for s in role["work_styles"]}
        shown = [impacts[s["slug"]] for s in g.role_styles]
        self.assertEqual(shown, sorted(shown, reverse=True))
        self.assertTrue(all(i >= 1.5 for i in shown))

    def test_nice_tasks_are_not_repeated(self):
        g = self.persona.grounding(self.archetypes["security_analyst"])
        listed = g.core_tasks + [t for ts in g.nice_tasks.values() for t in ts]
        self.assertEqual(len(listed), len(set(listed)))


class TestPrompt(DatastoreCase):

    def setUp(self):
        self.composer = AgentComposer(self.ds)

    def render(self, personality, archetype="security_analyst"):
        return self.composer.compose(agent(personality), archetype).render_system_prompt()

    def test_layers_render_and_flavor_stays_out(self):
        prompt = self.render({
            "work_styles": [{"style": "cautiousness", "level": 2}],
            "tone": [{"axis": "verbosity", "level": -2}, {"axis": "technicality", "level": 1}],
            "descriptors": ["Nonlinear", "Holistic"]})
        self.assertIn("# ROLE GROUNDING", prompt)
        self.assertIn("O*NET-SOC 15-1212.00", prompt)
        self.assertIn("NICE Framework work role Defensive Cybersecurity (PD-WRL-001)", prompt)
        self.assertIn("Above all - Cautiousness: be careful", prompt)
        self.assertEqual(prompt.count("Cautiousness:"), 1)          # emphasis replaces role line
        self.assertIn("- Terse: Give the answer", prompt)
        self.assertIn("- Lean Technical:", prompt)
        self.assertIn("do not already decide", prompt)               # contract wins
        for word in ("Nonlinear", "Holistic"):
            self.assertNotIn(word, prompt)

    def test_trait_items_never_reach_a_prompt(self):
        items = [i["text"] for t in self.ds.load_reference("trait") for i in t["items"]]
        for slug in ("linux_admin", "security_analyst", "backend_dev", "cat"):
            prompt = self.render({}, slug)
            self.assertFalse([i for i in items if i in prompt], slug)

    def test_ungrounded_archetype_has_no_persona_sections(self):
        prompt = self.render({}, "cat")
        self.assertNotIn("# ROLE GROUNDING", prompt)
        self.assertNotIn("# WORKING STYLE", prompt)


class TestMigrationAndGeneration(DatastoreCase):

    def setUp(self):
        self.persona = Persona(self.ds)

    def test_v1_words_map_through_lex_matches(self):
        new = self.persona.migrate({"temperament": "Methodical", "tone": "Concise",
                                    "traits": ["Iterative", "Adaptive"]})
        self.assertEqual(new["work_styles"], [{"style": "attention_to_detail", "level": 2}])
        self.assertEqual(new["tone"], [{"axis": "verbosity", "level": -2}])
        self.assertEqual(new["descriptors"], ["Iterative", "Adaptive", "Methodical", "Concise"])
        self.assertIs(self.persona.migrate(new), new)               # idempotent

    def test_words_without_a_close_match_stay_flavor(self):
        new = self.persona.migrate({"temperament": "Empirical", "tone": "Inquisitive",
                                    "traits": []})
        self.assertEqual((new["work_styles"], new["tone"]), ([], []))
        self.assertEqual(new["descriptors"], ["Empirical", "Inquisitive"])

    def test_every_generated_word_has_a_known_home(self):
        tones = set(lex.default().table("tones").values())
        temperaments = set(lex.default().table("temperaments").values())
        for axis in self.ds.load_reference("tone_axis"):
            self.assertTrue({m["term"] for m in axis["lex_matches"]} <= tones, axis["slug"])
        for style in self.ds.load_reference("work_style"):
            self.assertTrue(set(style["lex_matches"]) <= temperaments, style["slug"])

    def test_generated_personality_validates(self):
        record = AgentGenerator().generate()                 # a complete agent
        self.assertEqual(self.ds.check("agent", record), [])
        for seed in range(20):
            record["profile"]["personality"] = self.persona.generate(
                random.Random(seed), ["Dynamic"])
            self.assertEqual(self.ds.check("agent", record), [], seed)

    def test_temperament_is_derived_or_unknown(self):
        t = self.persona.temperament
        self.assertIsNone(t({"work_styles": [{"style": "cautiousness", "level": 2}]}))
        self.assertEqual(t({"work_styles": [{"style": "leadership_orientation", "level": 1}]}),
                         "driver")
        self.assertEqual(t({"work_styles": [{"style": "empathy", "level": 2}]}), "amiable")
        self.assertEqual(t({"work_styles": [{"style": "leadership_orientation", "level": 2},
                                            {"style": "social_orientation", "level": 1}]}),
                         "expressive")


class TestProvenance(unittest.TestCase):

    def test_sourced_tables_carry_attribution(self):
        for name in ("work_style", "work_role", "work_task"):
            doc = json.loads((REF / f"{name}.json").read_text(encoding="utf-8"))
            self.assertIn("O*NET 31.0 Database", doc["$comment"], name)
            self.assertIn("CC BY 4.0", doc["$comment"], name)
        trait = json.loads((REF / "trait.json").read_text(encoding="utf-8"))
        self.assertIn("public domain", trait["$comment"])


if __name__ == "__main__":
    unittest.main()
