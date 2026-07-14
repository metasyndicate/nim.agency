"""
DataStore - CRUD operations for the JSON-based datastore

Provides generic create, read, update, delete operations
for all entity types in the agency data model.
"""

import json
import os
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union
from dataclasses import dataclass, field

from .schema import SchemaManager, get_schema_manager


@dataclass
class QueryResult:
    """Result container for query operations."""
    data: list[dict]
    total: int
    offset: int = 0
    limit: Optional[int] = None

    def first(self) -> Optional[dict]:
        """Get first result or None."""
        return self.data[0] if self.data else None

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)


class DataStore:
    """
    JSON-based datastore with CRUD operations.

    Handles file-based storage with:
    - Reference data (lookup tables) in single files
    - Entity data (agents, missions) in individual files or collections

    Usage:
        ds = DataStore()

        # Load reference data
        ranks = ds.load_reference("rank")

        # Create an agent
        agent = ds.create("agent", agent_data)

        # Query agents
        results = ds.query("agent", lambda a: a["economy"]["xp"] > 100)

        # Update agent
        ds.update("agent", agent_id, {"economy": {"gold": 500}})

        # Delete agent
        ds.delete("agent", agent_id)
    """

    # Entity type to storage configuration
    ENTITY_CONFIG = {
        # Reference data: stored in single files with "data" array
        "status": {"type": "reference", "file": "status.json"},
        "rank": {"type": "reference", "file": "rank.json"},
        "class": {"type": "reference", "file": "class.json"},
        "skill_domain": {"type": "reference", "file": "skill_domain.json"},
        "tool_category": {"type": "reference", "file": "tool_category.json"},
        "achievement": {"type": "reference", "file": "achievement.json"},
        "mission_status": {"type": "reference", "file": "mission_status.json"},
        "tool": {"type": "reference", "file": "tools.json"},
        "qualifier": {"type": "reference", "file": "qualifier.json"},
        "ops_domain": {"type": "reference", "file": "ops_domain.json"},
        "focus": {"type": "reference", "file": "focus.json"},
        "specialty": {"type": "reference", "file": "specialty.json"},
        "capability": {"type": "reference", "file": "capability.json"},
        "archetype": {"type": "reference", "file": "archetype.json"},
        "instruction": {"type": "reference", "file": "instruction.json"},
        "crue_type": {"type": "reference", "file": "crue_type.json"},
        "agent_relation": {"type": "reference", "file": "agent_relation.json"},
        "config": {"type": "singleton", "file": "config.json"},

        # Entity data: stored in directories with individual files
        "agent": {"type": "collection", "dir": "agents", "id_field": "id"},
        "mission": {"type": "collection", "dir": "missions", "id_field": "id"},
        "group": {"type": "collection", "dir": "groups", "id_field": "id"},
        "transaction": {"type": "append", "file": "transactions/ledger.json"},
        "event": {"type": "append", "file": "events/log.json"},
        "peer_review": {"type": "append", "file": "reviews/reviews.json"},

        # Remote/Substations: stored in operator-local path (~/.nim/agency/)
        "substation": {
            "type": "collection",
            "dir": "substations",
            "id_field": "id",
            "local_storage": True
        },
        "credential": {
            "type": "collection",
            "dir": "vault/credentials",
            "id_field": "id",
            "local_storage": True,
            "encrypted": True  # Note: actual encryption handled by vault module
        },
        "remote_operation": {
            "type": "append",
            "file": "remote_operations.json",
            "local_storage": True
        },
    }

    # Local storage base path (operator-scoped)
    LOCAL_STORAGE_BASE = Path.home() / ".nim" / "agency"

    def __init__(self, base_path: Optional[str] = None, schema_manager: Optional[SchemaManager] = None):
        """
        Initialize datastore.

        Args:
            base_path: Root path to data directory
            schema_manager: Optional schema manager for validation
        """
        if base_path is None:
            lib_dir = Path(__file__).parent.parent.parent
            base_path = lib_dir / "data" / "agency" / "data"

        self.base_path = Path(base_path)
        self.local_path = self.LOCAL_STORAGE_BASE
        self.schema_manager = schema_manager or get_schema_manager()

        # Cache for reference data
        self._reference_cache: dict[str, list[dict]] = {}

    def _get_storage_path(self, entity_type: str) -> Path:
        """Get the base storage path for an entity type."""
        config = self.ENTITY_CONFIG.get(entity_type)
        if config and config.get("local_storage"):
            return self.local_path
        return self.base_path

    def _get_timestamp(self) -> str:
        """Get current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    def _generate_id(self) -> str:
        """Generate a new UUID."""
        return str(uuid.uuid4())

    # -------------------------------------------------------------------------
    # Reference Data Operations
    # -------------------------------------------------------------------------

    def load_reference(self, entity_type: str, force_reload: bool = False) -> list[dict]:
        """
        Load reference/lookup data.

        Args:
            entity_type: Type of reference data (rank, status, etc.)
            force_reload: Force reload from disk

        Returns:
            List of reference records
        """
        if not force_reload and entity_type in self._reference_cache:
            return self._reference_cache[entity_type]

        config = self.ENTITY_CONFIG.get(entity_type)
        if not config or config["type"] not in ("reference", "singleton"):
            raise ValueError(f"Unknown reference type: {entity_type}")

        file_path = self.base_path / "reference" / config["file"]
        if not file_path.exists():
            return []

        with open(file_path, "r") as f:
            content = json.load(f)

        # Handle both {"data": [...]} and direct array formats
        if isinstance(content, dict):
            data = content.get("data", [])
            # Singleton returns the data object directly
            if config["type"] == "singleton":
                self._reference_cache[entity_type] = [data] if isinstance(data, dict) else data
                return self._reference_cache[entity_type]
        else:
            data = content

        self._reference_cache[entity_type] = data if isinstance(data, list) else [data]
        return self._reference_cache[entity_type]

    def get_reference_by_id(self, entity_type: str, id_value: int) -> Optional[dict]:
        """Get a reference record by ID."""
        data = self.load_reference(entity_type)
        for record in data:
            if record.get("id") == id_value:
                return record
        return None

    def get_reference_by_slug(self, entity_type: str, slug: str) -> Optional[dict]:
        """Get a reference record by slug."""
        data = self.load_reference(entity_type)
        for record in data:
            if record.get("slug") == slug:
                return record
        return None

    def get_config(self) -> dict:
        """Get the agency configuration."""
        data = self.load_reference("config")
        return data[0] if data else {}

    # -------------------------------------------------------------------------
    # Collection CRUD Operations
    # -------------------------------------------------------------------------

    def create(self, entity_type: str, data: dict, validate: bool = True) -> dict:
        """
        Create a new entity.

        Args:
            entity_type: Entity type (agent, mission, etc.)
            data: Entity data
            validate: Whether to validate against schema

        Returns:
            Created entity with generated ID and timestamps
        """
        config = self.ENTITY_CONFIG.get(entity_type)
        if not config:
            raise ValueError(f"Unknown entity type: {entity_type}")

        # Generate ID if not provided
        id_field = config.get("id_field", "id")
        if id_field not in data or not data[id_field]:
            data[id_field] = self._generate_id()

        # Add audit timestamps
        now = self._get_timestamp()
        if "audit" not in data:
            data["audit"] = {}
        data["audit"]["created_at"] = now
        data["audit"]["updated_at"] = now
        data["audit"]["retired_at"] = None

        # Validate if requested
        if validate:
            errors = self.schema_manager.validate(entity_type, data)
            if errors:
                raise ValueError(f"Validation failed: {'; '.join(errors)}")

        # Save based on storage type
        if config["type"] == "collection":
            self._save_collection_item(entity_type, data)
        elif config["type"] == "append":
            self._append_to_log(entity_type, data)

        return data

    def read(self, entity_type: str, entity_id: str) -> Optional[dict]:
        """
        Read a single entity by ID.

        Args:
            entity_type: Entity type
            entity_id: Entity ID (UUID)

        Returns:
            Entity data or None if not found
        """
        config = self.ENTITY_CONFIG.get(entity_type)
        if not config or config["type"] != "collection":
            raise ValueError(f"Cannot read single item for type: {entity_type}")

        base = self._get_storage_path(entity_type)
        file_path = base / config["dir"] / f"{entity_id}.json"
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def update(self, entity_type: str, entity_id: str, updates: dict, merge: bool = True) -> Optional[dict]:
        """
        Update an existing entity.

        Args:
            entity_type: Entity type
            entity_id: Entity ID
            updates: Fields to update
            merge: Deep merge updates (True) or replace (False)

        Returns:
            Updated entity or None if not found
        """
        existing = self.read(entity_type, entity_id)
        if existing is None:
            return None

        if merge:
            data = self._deep_merge(existing, updates)
        else:
            data = {**existing, **updates}

        # Update timestamp
        if "audit" not in data:
            data["audit"] = {}
        data["audit"]["updated_at"] = self._get_timestamp()

        # Save
        config = self.ENTITY_CONFIG[entity_type]
        self._save_collection_item(entity_type, data)

        return data

    def delete(self, entity_type: str, entity_id: str, hard: bool = False) -> bool:
        """
        Delete an entity.

        Args:
            entity_type: Entity type
            entity_id: Entity ID
            hard: Permanently delete (True) or soft delete (False)

        Returns:
            True if deleted, False if not found
        """
        config = self.ENTITY_CONFIG.get(entity_type)
        if not config or config["type"] != "collection":
            raise ValueError(f"Cannot delete for type: {entity_type}")

        base = self._get_storage_path(entity_type)
        file_path = base / config["dir"] / f"{entity_id}.json"
        if not file_path.exists():
            return False

        if hard:
            file_path.unlink()
        else:
            # Soft delete: set retired_at
            entity = self.read(entity_type, entity_id)
            if entity:
                if "audit" not in entity:
                    entity["audit"] = {}
                entity["audit"]["retired_at"] = self._get_timestamp()
                self._save_collection_item(entity_type, entity)

        return True

    def query(
        self,
        entity_type: str,
        filter_fn: Optional[Callable[[dict], bool]] = None,
        sort_key: Optional[str] = None,
        sort_reverse: bool = False,
        offset: int = 0,
        limit: Optional[int] = None,
        include_retired: bool = False
    ) -> QueryResult:
        """
        Query entities with optional filtering and sorting.

        Args:
            entity_type: Entity type to query
            filter_fn: Optional filter function
            sort_key: Dot-notation path to sort by (e.g., "economy.xp")
            sort_reverse: Sort descending
            offset: Skip first N results
            limit: Maximum results to return
            include_retired: Include soft-deleted entities

        Returns:
            QueryResult with matching entities
        """
        config = self.ENTITY_CONFIG.get(entity_type)
        if not config:
            raise ValueError(f"Unknown entity type: {entity_type}")

        # Load all entities
        if config["type"] == "collection":
            entities = self._load_collection(entity_type)
        elif config["type"] in ("reference", "singleton"):
            entities = self.load_reference(entity_type)
        elif config["type"] == "append":
            entities = self._load_append_log(entity_type)
        else:
            entities = []

        # Filter out retired unless requested
        if not include_retired:
            entities = [
                e for e in entities
                if not e.get("audit", {}).get("retired_at")
            ]

        # Apply filter
        if filter_fn:
            entities = [e for e in entities if filter_fn(e)]

        total = len(entities)

        # Sort
        if sort_key:
            entities = sorted(
                entities,
                key=lambda x: self._get_nested(x, sort_key) or "",
                reverse=sort_reverse
            )

        # Paginate
        if offset:
            entities = entities[offset:]
        if limit:
            entities = entities[:limit]

        return QueryResult(data=entities, total=total, offset=offset, limit=limit)

    def count(self, entity_type: str, filter_fn: Optional[Callable[[dict], bool]] = None) -> int:
        """Count entities matching optional filter."""
        result = self.query(entity_type, filter_fn=filter_fn)
        return result.total

    def exists(self, entity_type: str, entity_id: str) -> bool:
        """Check if an entity exists."""
        config = self.ENTITY_CONFIG.get(entity_type)
        if not config or config["type"] != "collection":
            return False
        base = self._get_storage_path(entity_type)
        file_path = base / config["dir"] / f"{entity_id}.json"
        return file_path.exists()

    # -------------------------------------------------------------------------
    # Archive and Reset Operations
    # -------------------------------------------------------------------------

    def archive_collection(self, entity_type: str) -> tuple[str, int]:
        """
        Archive all items in a collection to a timestamped directory.

        Args:
            entity_type: Collection type to archive (e.g., 'agent')

        Returns:
            Tuple of (archive_path, item_count)
        """
        import shutil

        config = self.ENTITY_CONFIG.get(entity_type)
        if not config or config["type"] != "collection":
            raise ValueError(f"Cannot archive non-collection type: {entity_type}")

        source_dir = self.base_path / config["dir"]
        if not source_dir.exists():
            return ("", 0)

        # Count items
        items = list(source_dir.glob("*.json"))
        if not items:
            return ("", 0)

        # Create archive directory with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_dir = self.base_path / "archive" / f"{config['dir']}_{timestamp}"
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Move files to archive
        for item_path in items:
            shutil.move(str(item_path), str(archive_dir / item_path.name))

        return (str(archive_dir), len(items))

    def reset_collection(self, entity_type: str, archive_first: bool = True) -> tuple[Optional[str], int]:
        """
        Reset a collection by removing all items (optionally archiving first).

        Args:
            entity_type: Collection type to reset
            archive_first: Archive before deleting (default True)

        Returns:
            Tuple of (archive_path or None, deleted_count)
        """
        archive_path = None
        count = 0

        if archive_first:
            archive_path, count = self.archive_collection(entity_type)
        else:
            config = self.ENTITY_CONFIG.get(entity_type)
            if config and config["type"] == "collection":
                dir_path = self.base_path / config["dir"]
                if dir_path.exists():
                    items = list(dir_path.glob("*.json"))
                    count = len(items)
                    for item_path in items:
                        item_path.unlink()

        return (archive_path, count)

    def list_archives(self, entity_type: str) -> list[dict]:
        """
        List available archives for an entity type.

        Returns:
            List of archive info dicts with 'path', 'timestamp', 'count'
        """
        config = self.ENTITY_CONFIG.get(entity_type)
        if not config or config["type"] != "collection":
            return []

        archive_base = self.base_path / "archive"
        if not archive_base.exists():
            return []

        archives = []
        prefix = config["dir"] + "_"

        for archive_dir in sorted(archive_base.glob(f"{prefix}*"), reverse=True):
            if archive_dir.is_dir():
                items = list(archive_dir.glob("*.json"))
                timestamp_str = archive_dir.name.replace(prefix, "")
                archives.append({
                    "path": str(archive_dir),
                    "name": archive_dir.name,
                    "timestamp": timestamp_str,
                    "count": len(items)
                })

        return archives

    def save_config(self, config_data: dict) -> None:
        """
        Save updated configuration.

        Args:
            config_data: Configuration data to save
        """
        config_data["audit"]["updated_at"] = self._get_timestamp()

        file_path = self.base_path / "reference" / "config.json"
        with open(file_path, "w") as f:
            json.dump({"$schema": "../schema/core/config.schema.json", "data": config_data}, f, indent=2)

        # Clear cache
        if "config" in self._reference_cache:
            del self._reference_cache["config"]

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _save_collection_item(self, entity_type: str, data: dict) -> None:
        """Save an item to a collection directory."""
        config = self.ENTITY_CONFIG[entity_type]
        base = self._get_storage_path(entity_type)
        dir_path = base / config["dir"]
        dir_path.mkdir(parents=True, exist_ok=True)

        # Set restrictive permissions for local storage
        if config.get("local_storage") and os.name != 'nt':
            os.chmod(dir_path, 0o700)

        entity_id = data[config.get("id_field", "id")]
        file_path = dir_path / f"{entity_id}.json"

        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)

        # Set restrictive permissions for local storage files
        if config.get("local_storage") and os.name != 'nt':
            os.chmod(file_path, 0o600)

    def _load_collection(self, entity_type: str) -> list[dict]:
        """Load all items from a collection directory."""
        config = self.ENTITY_CONFIG[entity_type]
        base = self._get_storage_path(entity_type)
        dir_path = base / config["dir"]

        if not dir_path.exists():
            return []

        entities = []
        for file_path in dir_path.glob("*.json"):
            try:
                with open(file_path, "r") as f:
                    entities.append(json.load(f))
            except (json.JSONDecodeError, IOError):
                continue  # Skip corrupted files

        return entities

    def _append_to_log(self, entity_type: str, data: dict) -> None:
        """Append data to a log file."""
        config = self.ENTITY_CONFIG[entity_type]
        base = self._get_storage_path(entity_type)
        file_path = base / config["file"]
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Set restrictive permissions for local storage directories
        if config.get("local_storage") and os.name != 'nt':
            os.chmod(file_path.parent, 0o700)

        # Load existing or create new
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    content = json.load(f)
            except (json.JSONDecodeError, IOError):
                content = {"data": []}
        else:
            content = {"data": []}

        # Auto-increment ID
        existing_ids = [r.get("id", 0) for r in content.get("data", [])]
        next_id = max(existing_ids, default=0) + 1
        data["id"] = next_id

        content["data"].append(data)

        with open(file_path, "w") as f:
            json.dump(content, f, indent=2)

        # Set restrictive permissions for local storage files
        if config.get("local_storage") and os.name != 'nt':
            os.chmod(file_path, 0o600)

    def _load_append_log(self, entity_type: str) -> list[dict]:
        """Load all records from an append log."""
        config = self.ENTITY_CONFIG[entity_type]
        base = self._get_storage_path(entity_type)
        file_path = base / config["file"]

        if not file_path.exists():
            return []

        try:
            with open(file_path, "r") as f:
                content = json.load(f)
            return content.get("data", [])
        except (json.JSONDecodeError, IOError):
            return []

    def _deep_merge(self, base: dict, updates: dict) -> dict:
        """Deep merge two dictionaries."""
        result = base.copy()
        for key, value in updates.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _get_nested(self, data: dict, path: str) -> Any:
        """Get nested value by dot-notation path."""
        keys = path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value


# Module-level convenience instance
_default_store: Optional[DataStore] = None


def get_datastore() -> DataStore:
    """Get the default datastore instance."""
    global _default_store
    if _default_store is None:
        _default_store = DataStore()
    return _default_store
