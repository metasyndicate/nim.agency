"""
Persona - what an agent's role, working style, and voice put into its
system prompt (plan: .nim/work/plan.101426.persona.md).

    layer C  grounding    the archetype's work roles: an O*NET occupation
                          (description, Core tasks by importance) and any
                          NICE Framework work roles
    layer A  work styles  the styles the occupation rates highest (O*NET
                          Work Styles Impact) plus the agent's own emphases
    layer B  tone         the agent's level on each tone axis, rendered as
                          that pole's guidance
    layer D  flavor       lex words (descriptors, motto, bio): never here

Everything is read from reference tables (work_role, work_task, work_style,
tone_axis), so the content is edited as data, not code.
"""

import random
from dataclasses import dataclass, field
from typing import Optional

# Prompt budget per layer
CORE_TASKS = 5            # O*NET Core tasks shown, most important first
NICE_TASKS = 3            # tasks shown per NICE work role
ROLE_STYLES = 4           # highest-impact styles of the occupation
MIN_IMPACT = 1.5          # ... and only those that clearly help (WI, -3..+3)

# Derived temperament (P3): Merrill-Reid social styles as a display
# projection of work styles. Assertiveness <- Leadership Orientation;
# responsiveness <- Social Orientation / Empathy. Unknown when neither says.
ASSERTIVE = ("leadership_orientation",)
RESPONSIVE = ("social_orientation", "empathy")
SOCIAL_STYLES = {(True, False): "driver", (True, True): "expressive",
                 (False, True): "amiable", (False, False): "analytical"}


@dataclass
class Grounding:
    """Layer C and the occupation's styles for one archetype."""
    occupation: Optional[dict] = None          # O*NET work_role row
    nice_roles: list[dict] = field(default_factory=list)
    core_tasks: list[str] = field(default_factory=list)
    nice_tasks: dict[str, list[str]] = field(default_factory=dict)
    role_styles: list[dict] = field(default_factory=list)   # work_style rows

    @property
    def sources(self) -> list[str]:
        roles = ([self.occupation] if self.occupation else []) + self.nice_roles
        return [f"work_role:{r['slug']}" for r in roles]


class Persona:
    """Reads the persona tables once; renders layers for any agent."""

    def __init__(self, ds):
        self.ds = ds
        self.roles = {r["slug"]: r for r in ds.load_reference("work_role")}
        self.tasks = {r["slug"]: r for r in ds.load_reference("work_task")}
        self.styles = {r["slug"]: r for r in ds.load_reference("work_style")}
        self.axes = {r["slug"]: r for r in ds.load_reference("tone_axis")}

    # -- layer C ---------------------------------------------------------------

    def grounding(self, archetype: dict) -> Grounding:
        g = Grounding()
        for slug in archetype.get("work_role_slugs") or []:
            role = self.roles.get(slug)
            if not role:
                continue
            if role["framework"] == "onet" and g.occupation is None:
                g.occupation = role
                core = [t for t in role["tasks"] if t.get("type") == "Core"]
                core.sort(key=lambda t: -(t.get("importance") or 0))
                g.core_tasks = [self.tasks[t["task"]]["description"]
                                for t in core[:CORE_TASKS]]
                rated = sorted(role.get("work_styles", []), key=lambda s: -s["impact"])
                g.role_styles = [self.styles[s["style"]] for s in rated
                                 if s["impact"] >= MIN_IMPACT][:ROLE_STYLES]
            elif role["framework"] == "nice":
                g.nice_roles.append(role)
                shown = set(g.core_tasks).union(*g.nice_tasks.values())
                texts = [self.tasks[t["task"]]["description"] for t in role["tasks"]]
                g.nice_tasks[slug] = [t for t in texts if t not in shown][:NICE_TASKS]
        return g

    # -- layer A ---------------------------------------------------------------

    @staticmethod
    def style_line(style: dict) -> str:
        """O*NET's 'A tendency to be careful, ...' as 'Cautiousness: be careful, ...'"""
        text = style["description"]
        if text.startswith("A tendency to "):
            text = text[len("A tendency to "):]
        return f"{style['label']}: {text}"

    def emphases(self, personality: dict) -> list[tuple[dict, int]]:
        out = []
        for e in (personality or {}).get("work_styles") or []:
            style = self.styles.get(e.get("style"))
            if style:
                out.append((style, e.get("level", 1)))
        return out

    # -- layer B ---------------------------------------------------------------

    def tone_lines(self, personality: dict) -> list[str]:
        settings = (personality or {}).get("tone") or []
        order = {slug: a.get("sort_order", 0) for slug, a in self.axes.items()}
        lines = []
        for t in sorted(settings, key=lambda t: order.get(t.get("axis"), 99)):
            axis, level = self.axes.get(t.get("axis")), t.get("level", 0)
            if not axis or not level:
                continue
            pole = axis["high"] if level > 0 else axis["low"]
            strength = "" if abs(level) == 2 else "Lean "
            lines.append(f"{strength}{pole['label']}: {pole['guidance']}")
        return lines

    # -- display ---------------------------------------------------------------

    def temperament(self, personality: dict) -> Optional[str]:
        levels = {e["style"]: e["level"] for e in (personality or {}).get("work_styles") or []}
        assertive = max((levels.get(s, 0) for s in ASSERTIVE), default=0)
        responsive = max((levels.get(s, 0) for s in RESPONSIVE), default=0)
        if not assertive and not responsive:
            return None
        return SOCIAL_STYLES[(assertive > 0, responsive > 0)]

    def describe(self, personality: dict) -> dict:
        """Labels for screens: styles, tone poles, temperament, descriptors."""
        return {
            "work_styles": [f"{s['label']}{' (strong)' if lvl == 2 else ''}"
                            for s, lvl in self.emphases(personality)],
            "tone": [f"{(a['high'] if t['level'] > 0 else a['low'])['label']}"
                     f"{'' if abs(t['level']) == 2 else ' (lean)'}"
                     for t in (personality or {}).get("tone") or []
                     if (a := self.axes.get(t.get("axis")))],
            "temperament": self.temperament(personality),
            "descriptors": list((personality or {}).get("descriptors") or []),
        }

    # -- migration and generation ---------------------------------------------

    def migrate(self, old: dict) -> dict:
        """v1 {temperament, traits, tone} (lex words) -> v2. Tone words map
        through tone_axis.lex_matches, temperament words through
        work_style.lex_matches; every old word is kept as a descriptor."""
        if "work_styles" in (old or {}) or "descriptors" in (old or {}):
            return old                                    # already v2
        temperament, tone = (old or {}).get("temperament"), (old or {}).get("tone")
        styles = [{"style": slug, "level": 2} for slug, s in self.styles.items()
                  if temperament and temperament in s.get("lex_matches", [])][:1]
        tones = []
        for slug, axis in self.axes.items():
            for m in axis.get("lex_matches", []):
                if tone and m["term"] == tone:
                    tones.append({"axis": slug, "level": m["level"]})
        words = list((old or {}).get("traits") or [])
        for w in (temperament, tone):
            if w and w not in words:
                words.append(w)
        return {"work_styles": styles, "tone": tones, "descriptors": words[:8]}

    def generate(self, rng: random.Random, descriptors: list[str] = ()) -> dict:
        """A random v2 personality: one or two work-style emphases, two or
        three tone settings."""
        styles = rng.sample(sorted(self.styles), rng.randint(1, 2))
        axes = rng.sample(sorted(self.axes), rng.randint(2, 3))
        return {
            "work_styles": [{"style": s, "level": rng.choice((1, 2))} for s in styles],
            "tone": [{"axis": a, "level": rng.choice((-2, -1, 1, 2))} for a in axes],
            "descriptors": list(descriptors)[:8],
        }
