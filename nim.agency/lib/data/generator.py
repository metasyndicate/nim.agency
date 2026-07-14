"""
Agent Generator - Procedural generation of biomimetic agent profiles

Uses taxonomy data from the NIM Lexicon and randomization to create
unique agent identities with RPG-style attributes, skills, and backstories.
"""

import csv
import random
import string
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import os

from .crud import DataStore, get_datastore


class LexLoader:
    """
    Loads lexicon data from CSV files in the lex/ directory.

    Supports both simple single-column files (one value per line)
    and multi-column CSV files (extracts specified column).

    Caches loaded data to avoid repeated file reads.
    """

    _cache: dict[str, list[str]] = {}
    _lex_path: Optional[Path] = None

    @classmethod
    def get_lex_path(cls) -> Path:
        """Resolve the lex directory path."""
        if cls._lex_path is None:
            # Navigate from lib/data/ to project root, then to lex/
            module_path = Path(__file__).resolve()
            project_root = module_path.parent.parent.parent
            cls._lex_path = project_root / "lex"
        return cls._lex_path

    @classmethod
    def load(
        cls,
        filename: str,
        column: int = 0,
        skip_header: bool = True,
        cache: bool = True
    ) -> list[str]:
        """
        Load values from a lexicon CSV file.

        Args:
            filename: Name of the CSV file (with or without .csv extension)
            column: Column index to extract (0-based)
            skip_header: Whether to skip the first row
            cache: Whether to cache the results

        Returns:
            List of string values from the specified column
        """
        # Normalize filename
        if not filename.endswith('.csv'):
            filename = f"{filename}.csv"

        cache_key = f"{filename}:{column}"

        if cache and cache_key in cls._cache:
            return cls._cache[cache_key]

        filepath = cls.get_lex_path() / filename
        values = []

        if not filepath.exists():
            # Return empty list if file doesn't exist
            return values

        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)

            for i, row in enumerate(reader):
                # Skip header row if requested
                if skip_header and i == 0:
                    continue

                # Skip empty rows
                if not row:
                    continue

                # Extract value from specified column
                if len(row) > column:
                    value = row[column].strip()
                    # Skip empty values
                    if value:
                        values.append(value)

        if cache:
            cls._cache[cache_key] = values

        return values

    @classmethod
    def load_simple(cls, filename: str, cache: bool = True, column: int = 0) -> list[str]:
        """
        Load values from a simple single-column file (one value per line).

        Args:
            filename: Name of the file
            cache: Whether to cache the results
            column: Column index for CSV files (0-based)

        Returns:
            List of string values
        """
        if not filename.endswith('.csv'):
            filename = f"{filename}.csv"

        cache_key = f"{filename}:simple:{column}"

        if cache and cache_key in cls._cache:
            return cls._cache[cache_key]

        filepath = cls.get_lex_path() / filename
        values = []

        if not filepath.exists():
            return values

        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for i, row in enumerate(f):
                # Skip header
                if i == 0:
                    continue
                # Handle CSV with multiple columns
                if ',' in row:
                    parts = row.split(',')
                    value = parts[column].strip() if len(parts) > column else ''
                else:
                    value = row.strip()
                if value:
                    values.append(value)

        if cache:
            cls._cache[cache_key] = values

        return values

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the lexicon cache."""
        cls._cache.clear()


class AgentGenerator:
    """
    Generates random agent profiles with:
    - Procedural names and codenames from NIM Lexicon
    - Balanced skill point allocation
    - Randomized personality traits
    - Generated backstories with scientific/academic tone

    Usage:
        gen = AgentGenerator()

        # Generate a single agent
        agent = gen.generate()

        # Generate with specific parameters
        agent = gen.generate(rank_id=3, class_id=2)

        # Generate multiple agents
        agents = gen.generate_batch(10)

        # Just generate a name
        name = gen.random_name()
        codename = gen.random_codename()
    """

    def __init__(self, datastore: Optional[DataStore] = None, seed: Optional[int] = None):
        """
        Initialize the generator.

        Args:
            datastore: DataStore instance for reference data
            seed: Optional random seed for reproducibility
        """
        self.datastore = datastore or get_datastore()
        if seed is not None:
            random.seed(seed)

        # Cache reference data
        self._ranks = None
        self._classes = None
        self._statuses = None
        self._skill_domains = None
        self._qualifiers = None
        self._ops_domains = None
        self._focuses = None
        self._specialties = None
        self._capabilities = None
        self._tools = None
        self._archetypes = None
        self._config = None

        # Lazy-loaded lexicon data
        self._lex_cache = {}

    def _get_lex(self, key: str) -> list[str]:
        """
        Get lexicon data by key, loading from file if needed.

        Mapping:
            first_names -> bioentity.csv
            last_names -> surnames.csv
            titles -> titles.csv
            adjectives -> root.csv (codename adjectives)
            nouns -> system.csv (codename nouns)
            traits -> descriptor.csv (personality traits)
            temperaments -> temperaments.csv
            tones -> tones.csv
            interests -> academic.csv
            mottos -> mottos.csv
            zodiac -> constellation.csv (column 0 = constellation names)
            origins -> origins.csv
            bio_templates -> bio_templates.csv
        """
        if key in self._lex_cache:
            return self._lex_cache[key]

        # Map keys to files and load
        mapping = {
            'first_names': ('bioentity', 0),
            'last_names': ('surnames', 0),
            'titles': ('titles', 0),
            'adjectives': ('root', 0),
            'nouns': ('system', 0),
            'traits': ('descriptor', 0),
            'temperaments': ('temperaments', 0),
            'tones': ('tones', 0),
            'interests': ('academic', 0),
            'mottos': ('mottos', 0),
            'zodiac': ('constellation', 0),
            'origins': ('origins', 0),
            'bio_templates': ('bio_templates', 0),
        }

        if key not in mapping:
            return []

        filename, column = mapping[key]
        values = LexLoader.load_simple(filename)

        self._lex_cache[key] = values
        return values

    def _load_references(self) -> None:
        """Load reference data from datastore."""
        if self._ranks is None:
            self._ranks = self.datastore.load_reference("rank")
            self._classes = self.datastore.load_reference("class")
            self._statuses = self.datastore.load_reference("status")
            self._skill_domains = self.datastore.load_reference("skill_domain")
            self._qualifiers = self.datastore.load_reference("qualifier")
            self._ops_domains = self.datastore.load_reference("ops_domain")
            self._focuses = self.datastore.load_reference("focus")
            self._specialties = self.datastore.load_reference("specialty")
            self._capabilities = self.datastore.load_reference("capability")
            self._tools = self.datastore.load_reference("tool")
            self._archetypes = self.datastore.load_reference("archetype")
            self._config = self.datastore.get_config()

    def _select_weighted_class(self) -> int:
        """Select a class_id using weighted random selection."""
        self._load_references()
        if not self._classes:
            return 1

        classes = self._classes
        weights = [c.get("weight", 1) for c in classes]
        selected = random.choices(classes, weights=weights, k=1)[0]
        return selected.get("id", 1)

    def _select_ops_domain(self) -> int:
        """Select an operational domain (facility/network/platform/application/data)."""
        self._load_references()
        if not self._ops_domains:
            return 1
        # Slight bias toward platform and application (where most agents live)
        weights = [1, 2, 3, 3, 2]  # FAC, NET, PLT, APP, DAT
        if len(self._ops_domains) != len(weights):
            weights = [1] * len(self._ops_domains)
        selected = random.choices(self._ops_domains, weights=weights, k=1)[0]
        return selected.get("id", 1)

    def _select_focus(self, ops_domain_id: int) -> int:
        """Select a focus area within the operational domain."""
        self._load_references()
        if not self._focuses:
            return 1
        # Filter focuses for this ops_domain
        matching = [f for f in self._focuses if f.get("ops_domain_id") == ops_domain_id]
        if not matching:
            return 1
        return random.choice(matching).get("id", 1)

    def _get_qualifier_for_xp(self, xp: int) -> int:
        """Determine qualifier_id based on XP."""
        self._load_references()
        if not self._qualifiers:
            return 1
        for q in sorted(self._qualifiers, key=lambda x: x.get("min_xp", 0), reverse=True):
            min_xp = q.get("min_xp", 0)
            max_xp = q.get("max_xp")
            if xp >= min_xp and (max_xp is None or xp <= max_xp):
                return q.get("id", 1)
        return 1

    def _get_rank_for_xp(self, xp: int) -> int:
        """Determine rank_id based on XP thresholds."""
        self._load_references()
        if not self._ranks:
            return 1
        # Find highest rank where xp >= threshold
        for r in sorted(self._ranks, key=lambda x: x.get("xp_threshold", 0), reverse=True):
            if xp >= r.get("xp_threshold", 0):
                return r.get("id", 1)
        return 1

    def _random_xp(self) -> int:
        """
        Generate random XP with weighted distribution favoring lower ranks.

        Distribution (approximate):
        - 25% Grunt (0-99)
        - 25% Tinkerer (100-249)
        - 20% Operator (250-749)
        - 15% Ranger (750-1999)
        - 8% Synthesizer/Theorist (2000-4999)
        - 5% Neckbeard (5000-9999)
        - 2% Berserker (10000+)
        """
        roll = random.random()
        if roll < 0.25:
            return random.randint(0, 99)
        elif roll < 0.50:
            return random.randint(100, 249)
        elif roll < 0.70:
            return random.randint(250, 749)
        elif roll < 0.85:
            return random.randint(750, 1999)
        elif roll < 0.93:
            return random.randint(2000, 4999)
        elif roll < 0.98:
            return random.randint(5000, 9999)
        else:
            return random.randint(10000, 15000)

    def _random_status(self) -> int:
        """
        Generate random status with weighted distribution.

        Distribution:
        - 10% Standby (1) - Ready but not active
        - 55% Operational (2) - Normal active duty
        - 15% Deployed (3) - Currently on mission
        - 8% Recovery (4) - Recuperating
        - 7% Suspended (5) - Temporarily offline
        - 3% Retired (6) - Honorably discharged
        - 2% Burned (7) - Compromised/destroyed
        """
        weights = [10, 55, 15, 8, 7, 3, 2]
        statuses = [1, 2, 3, 4, 5, 6, 7]
        return random.choices(statuses, weights=weights, k=1)[0]

    def _scale_gold(self, xp: int, base_gold: int) -> int:
        """
        Scale starting gold based on XP (career earnings).

        Higher ranked agents have accumulated more gold over their career.
        Returns gold with some random variation.
        """
        # Base gold + XP-based bonus + random variation
        xp_bonus = int(xp * 0.1)  # 10% of XP as gold bonus
        variation = random.randint(-20, 50)
        return max(0, base_gold + xp_bonus + variation)

    def compute_role_title(self, qualifier_id: int, focus_id: int, rank_id: int) -> str:
        """
        Compute the compound role title from qualifier + focus + rank.

        Examples:
            - "Rookie Firewall Tinkerer"
            - "Senior Kubernetes Operator"
            - "Burnout Database Neckbeard"
        """
        self._load_references()

        qualifier = next((q for q in (self._qualifiers or []) if q.get("id") == qualifier_id), None)
        focus = next((f for f in (self._focuses or []) if f.get("id") == focus_id), None)
        rank = next((r for r in (self._ranks or []) if r.get("id") == rank_id), None)

        q_label = qualifier.get("label", "Rookie") if qualifier else "Rookie"
        f_label = focus.get("label", "Platform") if focus else "Platform"
        r_label = rank.get("label", "Grunt") if rank else "Grunt"

        return f"{q_label} {f_label} {r_label}"

    def _select_specialty(self, ops_domain_id: int) -> Optional[int]:
        """
        Optionally select a specialty matching the ops_domain.

        50% chance of having a specialty - not everyone gets to be special.
        """
        self._load_references()
        if not self._specialties or random.random() < 0.5:
            return None

        # Filter specialties that match the ops_domain
        matching = [s for s in self._specialties if s.get("ops_domain_id") == ops_domain_id]
        if not matching:
            return None

        return random.choice(matching).get("id")

    def get_specialty_label(self, specialty_id: Optional[int]) -> Optional[str]:
        """Get the label for a specialty by ID."""
        if specialty_id is None:
            return None
        self._load_references()
        specialty = next((s for s in (self._specialties or []) if s.get("id") == specialty_id), None)
        return specialty.get("label") if specialty else None

    def _select_archetype(self, focus_id: int) -> int:
        """
        Select an archetype based on focus area.

        Maps focus areas to appropriate archetypes (Linux Admin, DevOps, SRE, etc.)
        based on the archetype's focus_ids list.
        """
        self._load_references()
        if not self._archetypes:
            return 1

        # Find archetypes that include this focus_id
        matching = [a for a in self._archetypes if focus_id in a.get("focus_ids", [])]

        if matching:
            # Pick randomly from matching archetypes
            return random.choice(matching).get("id", 1)

        # Fallback: map ops_domain to general archetype
        # Get focus's ops_domain
        focus = next((f for f in (self._focuses or []) if f.get("id") == focus_id), None)
        ops_domain_id = focus.get("ops_domain_id", 3) if focus else 3

        # Default archetypes by ops_domain
        domain_defaults = {
            1: 1,   # Facility -> Linux Admin
            2: 3,   # Network -> Network Engineer
            3: 6,   # Platform -> Platform Engineer
            4: 2,   # Application -> DevOps Engineer
            5: 9,   # Data -> Data Engineer
        }

        return domain_defaults.get(ops_domain_id, 1)

    def get_archetype(self, archetype_id: int) -> Optional[dict]:
        """Get archetype data by ID."""
        self._load_references()
        return next((a for a in (self._archetypes or []) if a.get("id") == archetype_id), None)

    def random_name(self) -> tuple[str, str, str, str]:
        """
        Generate a random full name with required title prefix.

        Returns:
            Tuple of (full_name, title, first_name, last_name)
        """
        first_names = self._get_lex('first_names')
        last_names = self._get_lex('last_names')
        titles = self._get_lex('titles')

        first = random.choice(first_names) if first_names else "Agent"
        last = random.choice(last_names) if last_names else "Unknown"
        title = random.choice(titles) if titles else "Agt."

        full_name = f"{title} {first} {last}"
        return (full_name, title, first, last)

    @staticmethod
    def compute_dogtag(title: str, first_name: str, last_name: str) -> str:
        """
        Compute a normalized dogtag from name components.

        The dogtag is a lowercased, dot-delimited identifier suitable for
        file/data/code tagging. Removes punctuation and normalizes whitespace.

        Example: ("Prof.", "Foo", "Barbaz") -> "prof.foo.barbaz"

        Args:
            title: Honorific/title prefix (e.g., 'Dr.', 'Prof.')
            first_name: First/given name
            last_name: Last/family name

        Returns:
            Normalized dogtag string
        """
        import re

        def normalize(s: str) -> str:
            # Lowercase
            s = s.lower()
            # Remove periods and other punctuation (keep alphanumeric, hyphens)
            s = re.sub(r'[^\w\s-]', '', s)
            # Replace spaces with hyphens, collapse multiple hyphens
            s = re.sub(r'\s+', '-', s.strip())
            s = re.sub(r'-+', '-', s)
            return s

        parts = [normalize(p) for p in [title, first_name, last_name] if p]
        # Filter out empty parts
        parts = [p for p in parts if p]
        return '.'.join(parts)

    def random_codename(self) -> str:
        """Generate a random codename (e.g., 'stellar.synthesizer')."""
        adjectives = self._get_lex('adjectives')
        nouns = self._get_lex('nouns')

        adj = random.choice(adjectives).lower() if adjectives else "unknown"
        noun = random.choice(nouns).lower() if nouns else "entity"

        # Clean up multi-word values - replace spaces with hyphens
        adj = adj.replace(' ', '-')
        noun = noun.replace(' ', '-')

        # Remove any digits from the components
        adj = ''.join(c for c in adj if not c.isdigit())
        noun = ''.join(c for c in noun if not c.isdigit())

        return f"{adj}.{noun}"

    def next_tag(self) -> int:
        """
        Generate the next sequential agent tag number.

        Queries existing agents to find the highest tag and returns
        the next value. Tags are 1-9999, zero-padded to 4 digits for display.
        """
        # Query all agents to find the maximum tag
        results = self.datastore.query("agent")
        max_tag = 0

        for agent in results.data:
            tag = agent.get("identity", {}).get("tag", 0)
            if isinstance(tag, int) and tag > max_tag:
                max_tag = tag

        return max_tag + 1

    def random_skills(self, total_points: int = 60, num_domains: Optional[int] = None) -> list[dict]:
        """
        Generate random skill allocations.

        Args:
            total_points: Total skill points to allocate
            num_domains: Number of domains to allocate to (None = all)

        Returns:
            List of skill allocations
        """
        self._load_references()

        domains = self._skill_domains or []
        if not domains:
            return []

        # Select domains to allocate
        if num_domains and num_domains < len(domains):
            selected = random.sample(domains, num_domains)
        else:
            selected = domains

        # Distribute points with some randomization
        allocations = []
        remaining = total_points
        domain_count = len(selected)

        for i, domain in enumerate(selected):
            if i == domain_count - 1:
                # Last domain gets remaining points
                points = min(remaining, domain.get("max_points", 20))
            else:
                # Random allocation with bias toward even distribution
                max_for_domain = min(remaining, domain.get("max_points", 20))
                avg = remaining // (domain_count - i)
                points = random.randint(max(0, avg - 5), min(max_for_domain, avg + 5))

            if points > 0:
                allocations.append({
                    "domain_id": domain["id"],
                    "points": points
                })
                remaining -= points

        return allocations

    def random_capabilities(
        self,
        focus_id: int,
        rank_id: int,
        num_capabilities: int = 5
    ) -> list[dict]:
        """
        Generate random capability assignments based on focus and rank.

        Args:
            focus_id: Agent's focus area (influences which capabilities are primary)
            rank_id: Agent's rank (influences max proficiency)
            num_capabilities: Number of capabilities to assign

        Returns:
            List of capability assignments with proficiency levels
        """
        self._load_references()

        if not self._capabilities:
            return []

        # Map focus areas to relevant capability categories
        focus = next((f for f in (self._focuses or []) if f.get("id") == focus_id), None)
        focus_slug = focus.get("slug", "") if focus else ""

        # Category weights based on focus
        category_weights = {
            "execution": 1.0,
            "analysis": 1.0,
            "platform": 1.0,
            "automation": 1.0,
            "data": 1.0,
            "security": 1.0,
            "development": 1.0,
            "communication": 0.5,
        }

        # Boost weights based on focus domain
        if focus_slug in ("firewall", "vpn", "core", "edge"):
            category_weights["security"] = 2.0
            category_weights["analysis"] = 1.5
        elif focus_slug in ("container", "kubernetes", "vm", "server"):
            category_weights["platform"] = 2.0
            category_weights["automation"] = 1.5
        elif focus_slug in ("database", "warehouse", "etl", "lake"):
            category_weights["data"] = 2.0
        elif focus_slug in ("service", "api", "middleware"):
            category_weights["development"] = 2.0
        elif focus_slug in ("pipeline", "config"):
            category_weights["automation"] = 2.0

        # Calculate weighted selection
        capabilities_with_weights = []
        for cap in self._capabilities:
            cat = cap.get("category", "execution")
            weight = category_weights.get(cat, 1.0)
            capabilities_with_weights.append((cap, weight))

        # Select capabilities
        selected = []
        available = capabilities_with_weights.copy()

        for _ in range(min(num_capabilities, len(available))):
            if not available:
                break
            weights = [w for _, w in available]
            chosen_idx = random.choices(range(len(available)), weights=weights, k=1)[0]
            chosen_cap, _ = available.pop(chosen_idx)
            selected.append(chosen_cap)

        # Assign proficiency levels based on rank
        # Higher rank = higher max proficiency
        max_prof_by_rank = {1: 2, 2: 3, 3: 3, 4: 4, 5: 4, 6: 5, 7: 5, 8: 5}
        max_prof = max_prof_by_rank.get(rank_id, 3)

        allocations = []
        for cap in selected:
            # Random proficiency between 1 and max for this rank
            # Primary capability (first one) gets higher proficiency
            if cap == selected[0]:
                proficiency = random.randint(max(1, max_prof - 1), max_prof)
            else:
                proficiency = random.randint(1, max(1, max_prof - 1))

            allocations.append({
                "capability_id": cap["id"],
                "proficiency": proficiency
            })

        return allocations

    def random_starter_tools(self, rank_id: int, gold_budget: int = 100) -> list[dict]:
        """
        Assign starter tools based on rank and budget.

        Args:
            rank_id: Agent's rank (affects which tools are available)
            gold_budget: Starting gold budget for tools

        Returns:
            List of tool assignments
        """
        self._load_references()

        if not self._tools:
            return []

        from datetime import datetime, timezone

        assignments = []
        spent = 0

        # Filter tools by rank requirement and sort by cost
        available = []
        for tool in self._tools:
            req = tool.get("requirements", {})
            min_rank = req.get("min_rank_id", 1)
            cost = tool.get("cost", 0)

            if rank_id >= min_rank and cost <= (gold_budget - spent):
                available.append(tool)

        # Always include free essentials
        essentials = ["bash", "python", "grep", "ping", "dig", "ps", "top"]
        for tool in self._tools:
            if tool["slug"] in essentials and tool not in available:
                available.append(tool)

        # Sort by cost (free first)
        available.sort(key=lambda t: t.get("cost", 0))

        # Assign free tools first
        for tool in available:
            if tool.get("cost", 0) == 0:
                assignments.append({
                    "tool_id": tool["id"],
                    "assigned_at": datetime.now(timezone.utc).isoformat(),
                    "assigned_by": "generator",
                    "proficiency": 3
                })

        # Then some paid tools within budget
        paid_tools = [t for t in available if t.get("cost", 0) > 0]
        random.shuffle(paid_tools)

        for tool in paid_tools[:3]:  # Max 3 additional paid tools
            cost = tool.get("cost", 0)
            if spent + cost <= gold_budget:
                assignments.append({
                    "tool_id": tool["id"],
                    "assigned_at": datetime.now(timezone.utc).isoformat(),
                    "assigned_by": "generator",
                    "proficiency": 2
                })
                spent += cost

        return assignments, spent

    def random_personality(self) -> dict:
        """Generate random personality attributes from lexicon."""
        traits = self._get_lex('traits')
        temperaments = self._get_lex('temperaments')
        tones = self._get_lex('tones')

        return {
            "temperament": random.choice(temperaments) if temperaments else "Analytical",
            "traits": random.sample(traits, min(random.randint(2, 4), len(traits))) if traits else [],
            "tone": random.choice(tones) if tones else "Formal"
        }

    def random_bio(self, name: str, missions: int = 0) -> str:
        """Generate a random biography using lexicon templates."""
        templates = self._get_lex('bio_templates')
        traits = self._get_lex('traits')
        origins = self._get_lex('origins')
        interests = self._get_lex('interests')

        if not templates:
            # Fallback template
            return f"{name} is a dedicated research operative with demonstrated proficiency in analytical methodologies."

        template = random.choice(templates)
        selected_traits = random.sample(traits, min(4, len(traits))) if traits else ["analytical", "methodical", "precise", "systematic"]

        try:
            return template.format(
                name=name.split()[-1],  # Use last name
                origin=random.choice(origins) if origins else "Research Division",
                trait1=selected_traits[0].lower() if selected_traits else "analytical",
                trait2=selected_traits[1].lower() if len(selected_traits) > 1 else "methodical",
                trait3=selected_traits[2].lower() if len(selected_traits) > 2 else "precise",
                trait4=selected_traits[3].lower() if len(selected_traits) > 3 else "systematic",
                missions=missions or random.randint(0, 50),
                interest=random.choice(interests) if interests else "computational research"
            )
        except (KeyError, IndexError):
            # Fallback if template formatting fails
            return f"{name} is a distinguished operative from the {random.choice(origins) if origins else 'Research Division'} sector."

    def generate(
        self,
        name: Optional[str] = None,
        codename: Optional[str] = None,
        rank_id: Optional[int] = None,
        class_id: Optional[int] = None,
        status_id: Optional[int] = None,
        ops_domain_id: Optional[int] = None,
        xp: Optional[int] = None,
        skill_points: int = 60,
        starting_gold: Optional[int] = None,
        include_profile: bool = True
    ) -> dict:
        """
        Generate a complete agent profile.

        Args:
            name: Override generated name
            codename: Override generated codename
            rank_id: Specific rank (default: grunt)
            class_id: Specific class (default: Class I)
            status_id: Specific status (default: standby)
            ops_domain_id: Operational domain (facility/network/platform/application)
            xp: Starting XP (determines qualifier)
            skill_points: Points to allocate to skills
            starting_gold: Starting gold amount
            include_profile: Include extended profile data

        Returns:
            Complete agent data structure
        """
        self._load_references()

        # Get defaults from config
        defaults = (self._config or {}).get("defaults", {})

        # Generate identity
        if name:
            # Parse provided name - assume "Title First Last" format
            parts = name.split(None, 2)
            if len(parts) >= 3:
                gen_title, gen_first, gen_last = parts[0], parts[1], parts[2]
            elif len(parts) == 2:
                # No title provided, assign one
                titles = self._get_lex('titles')
                gen_title = random.choice(titles) if titles else "Agt."
                gen_first, gen_last = parts[0], parts[1]
            else:
                # Single name, generate rest
                titles = self._get_lex('titles')
                gen_title = random.choice(titles) if titles else "Agt."
                gen_first = parts[0]
                gen_last = "Unknown"
            gen_name = f"{gen_title} {gen_first} {gen_last}"
        else:
            gen_name, gen_title, gen_first, gen_last = self.random_name()

        gen_dogtag = self.compute_dogtag(gen_title, gen_first, gen_last)
        gen_codename = codename or self.random_codename()

        # Ensure unique codename by appending alphabetic suffix if collision
        existing = self.datastore.query(
            "agent",
            filter_fn=lambda a: a.get("identity", {}).get("codename") == gen_codename
        )
        if existing.total > 0:
            # Append a random two-letter suffix (e.g., "stellar.nova.zx")
            suffix = random.choice(string.ascii_lowercase) + random.choice(string.ascii_lowercase)
            gen_codename = f"{gen_codename}.{suffix}"

        # Generate random XP and status if not specified
        gen_xp = xp if xp is not None else self._random_xp()
        gen_status_id = status_id if status_id is not None else self._random_status()

        # Determine classification IDs
        # If rank_id not specified, derive from XP
        gen_rank_id = rank_id if rank_id is not None else self._get_rank_for_xp(gen_xp)
        gen_ops_domain_id = ops_domain_id or self._select_ops_domain()
        gen_focus_id = self._select_focus(gen_ops_domain_id)
        gen_qualifier_id = self._get_qualifier_for_xp(gen_xp)
        gen_specialty_id = self._select_specialty(gen_ops_domain_id)
        gen_archetype_id = self._select_archetype(gen_focus_id)

        # Compute the compound role title (e.g., "Rookie Firewall Tinkerer")
        gen_role_title = self.compute_role_title(gen_qualifier_id, gen_focus_id, gen_rank_id)

        # If agent has a specialty, append it for extra flavor
        specialty_label = self.get_specialty_label(gen_specialty_id)

        # Build agent structure
        agent = {
            "id": str(uuid.uuid4()),
            "identity": {
                "name": gen_name,
                "codename": gen_codename,
                "tag": self.next_tag(),
                "title": gen_title,
                "dogtag": gen_dogtag,
                "role_title": gen_role_title,
                "specialty": specialty_label,
                "aliases": []
            },
            "classification": {
                "status_id": gen_status_id,
                "rank_id": gen_rank_id,
                "class_id": class_id or self._select_weighted_class(),
                "ops_domain_id": gen_ops_domain_id,
                "focus_id": gen_focus_id,
                "qualifier_id": gen_qualifier_id,
                "specialty_id": gen_specialty_id,
                "archetype_id": gen_archetype_id
            },
            "economy": {
                "health": {
                    "current": defaults.get("health", 100),
                    "max": defaults.get("health", 100)
                },
                "xp": gen_xp,
                "gold": starting_gold if starting_gold is not None else self._scale_gold(gen_xp, defaults.get("starting_gold", 50)),
                "skill_points": {
                    "total": defaults.get("skill_points", 100),
                    "assigned": skill_points
                }
            },
            "skills": self.random_skills(skill_points),
            "capabilities": self.random_capabilities(gen_focus_id, gen_rank_id),
            "tools": [],  # Will be populated below
            "achievements": [],
            "ratings": {
                "operator": {"sum": 0, "count": 0},
                "peer": {"sum": 0, "count": 0},
                "missions_completed": 0
            },
            "origin": {
                "operator": os.environ.get("USER", "system"),
                "host": os.environ.get("HOSTNAME", "localhost"),
                "client": "generator",
                "discovery": "created",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": None,
                "model": None
            },
            "groups": [],
            "audit": {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "retired_at": None
            }
        }

        # Assign starter tools (deduct from gold)
        tool_budget = min(100, agent["economy"]["gold"])
        starter_tools, tool_spend = self.random_starter_tools(gen_rank_id, tool_budget)
        agent["tools"] = starter_tools
        agent["economy"]["gold"] -= tool_spend

        # Add extended profile if requested
        if include_profile:
            mottos = self._get_lex('mottos')
            interests = self._get_lex('interests')
            zodiac = self._get_lex('zodiac')

            agent["profile"] = {
                "bio": self.random_bio(gen_name),
                "avatar": None,
                "motto": random.choice(mottos) if mottos else "Knowledge through observation.",
                "personality": self.random_personality(),
                "interests": random.sample(interests, min(random.randint(2, 4), len(interests))) if interests else [],
                "zodiac": random.choice(zodiac) if zodiac else "Orion",
                "quotes": []
            }

        return agent

    def generate_batch(self, count: int, **kwargs) -> list[dict]:
        """
        Generate multiple agents.

        Args:
            count: Number of agents to generate
            **kwargs: Arguments passed to generate()

        Returns:
            List of generated agents
        """
        return [self.generate(**kwargs) for _ in range(count)]

    def create_and_save(self, **kwargs) -> dict:
        """Generate an agent and save to datastore."""
        agent = self.generate(**kwargs)
        return self.datastore.create("agent", agent, validate=False)


# Module-level convenience
_default_generator: Optional[AgentGenerator] = None


def get_generator() -> AgentGenerator:
    """Get the default generator instance."""
    global _default_generator
    if _default_generator is None:
        _default_generator = AgentGenerator()
    return _default_generator
