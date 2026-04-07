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

    def random_name(self, include_prefix: bool = True) -> str:
        """Generate a random full name from lexicon data."""
        first_names = self._get_lex('first_names')
        last_names = self._get_lex('last_names')
        titles = self._get_lex('titles')

        first = random.choice(first_names) if first_names else "Agent"
        last = random.choice(last_names) if last_names else "Unknown"

        if include_prefix and titles and random.random() < 0.25:
            # 25% chance of having a title prefix
            prefix = random.choice(titles)
            return f"{prefix} {first} {last}"

        return f"{first} {last}"

    def random_codename(self) -> str:
        """Generate a random codename (e.g., 'stellar.synthesizer')."""
        adjectives = self._get_lex('adjectives')
        nouns = self._get_lex('nouns')

        adj = random.choice(adjectives).lower() if adjectives else "unknown"
        noun = random.choice(nouns).lower() if nouns else "entity"

        # Clean up multi-word values
        adj = adj.replace(' ', '-')
        noun = noun.replace(' ', '-')

        # Occasionally add a numeric suffix
        if random.random() < 0.3:
            suffix = random.randint(1, 99)
            return f"{adj}.{noun}.{suffix:02d}"

        return f"{adj}.{noun}"

    def random_tag(self) -> int:
        """Generate a random agent tag number."""
        return random.randint(1, 9999)

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
        skill_points: int = 60,
        starting_gold: Optional[int] = None,
        include_profile: bool = True
    ) -> dict:
        """
        Generate a complete agent profile.

        Args:
            name: Override generated name
            codename: Override generated codename
            rank_id: Specific rank (default: recruit)
            class_id: Specific class (default: Class I)
            status_id: Specific status (default: standby)
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
        gen_name = name or self.random_name()
        gen_codename = codename or self.random_codename()

        # Ensure unique codename
        existing = self.datastore.query(
            "agent",
            filter_fn=lambda a: a.get("identity", {}).get("codename") == gen_codename
        )
        if existing.total > 0:
            gen_codename = f"{gen_codename}.{random.randint(10, 99)}"

        # Build agent structure
        agent = {
            "id": str(uuid.uuid4()),
            "identity": {
                "name": gen_name,
                "codename": gen_codename,
                "tag": self.random_tag(),
                "title": None,
                "aliases": []
            },
            "classification": {
                "status_id": status_id or defaults.get("status_id", 1),
                "rank_id": rank_id or defaults.get("rank_id", 1),
                "class_id": class_id or self._select_weighted_class()
            },
            "economy": {
                "health": {
                    "current": defaults.get("health", 100),
                    "max": defaults.get("health", 100)
                },
                "xp": 0,
                "gold": starting_gold if starting_gold is not None else defaults.get("starting_gold", 50),
                "skill_points": {
                    "total": defaults.get("skill_points", 100),
                    "assigned": skill_points
                }
            },
            "skills": self.random_skills(skill_points),
            "tools": [],
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
