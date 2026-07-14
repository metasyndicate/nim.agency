"""
Schema Manager - Load, validate, and inspect JSON schemas

Provides schema loading, validation against JSON Schema 2020-12,
and introspection utilities for the agency data model.
"""

import json
import os
from pathlib import Path
from typing import Any, Optional, Union
from datetime import datetime

try:
    import jsonschema
    from jsonschema import Draft202012Validator, RefResolver
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


class SchemaManager:
    """
    Manages JSON schema loading, caching, and validation.

    Usage:
        sm = SchemaManager()
        sm.load_all()

        # Validate data against schema
        errors = sm.validate("agent", agent_data)

        # Get schema definition
        schema = sm.get_schema("agent")

        # List available schemas
        schemas = sm.list_schemas()
    """

    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize schema manager.

        Args:
            base_path: Root path to data directory. Defaults to ./data/agency
        """
        if base_path is None:
            # Resolve relative to this file's location
            lib_dir = Path(__file__).parent.parent.parent
            base_path = lib_dir / "data" / "agency"

        self.base_path = Path(base_path)
        self.schema_path = self.base_path / "schema"
        self.data_path = self.base_path / "data"

        # Schema cache
        self._schemas: dict[str, dict] = {}
        self._index: Optional[dict] = None
        self._loaded = False

    def load_all(self) -> "SchemaManager":
        """Load all schemas from the schema directory."""
        self._load_index()

        for category in ["meta", "reference", "core"]:
            category_path = self.schema_path / category
            if category_path.exists():
                for schema_file in category_path.glob("*.schema.json"):
                    name = schema_file.stem.replace(".schema", "")
                    self._load_schema(name, schema_file)

        self._loaded = True
        return self

    def _load_index(self) -> None:
        """Load the schema index."""
        index_path = self.schema_path / "index.json"
        if index_path.exists():
            with open(index_path, "r") as f:
                self._index = json.load(f)

    def _load_schema(self, name: str, path: Path) -> dict:
        """Load a single schema file."""
        with open(path, "r") as f:
            schema = json.load(f)
        self._schemas[name] = schema
        return schema

    def get_schema(self, name: str) -> Optional[dict]:
        """
        Get a schema by name.

        Args:
            name: Schema name (e.g., 'agent', 'rank', 'status')

        Returns:
            Schema dictionary or None if not found
        """
        if not self._loaded:
            self.load_all()
        return self._schemas.get(name)

    def list_schemas(self) -> list[str]:
        """List all available schema names."""
        if not self._loaded:
            self.load_all()
        return sorted(self._schemas.keys())

    def get_index(self) -> Optional[dict]:
        """Get the schema index."""
        if self._index is None:
            self._load_index()
        return self._index

    def validate(self, schema_name: str, data: dict) -> list[str]:
        """
        Validate data against a schema.

        Args:
            schema_name: Name of schema to validate against
            data: Data dictionary to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        if not HAS_JSONSCHEMA:
            return ["jsonschema library not installed - validation skipped"]

        schema = self.get_schema(schema_name)
        if schema is None:
            return [f"Schema '{schema_name}' not found"]

        errors = []
        try:
            # Create resolver for $ref handling
            resolver = RefResolver(
                base_uri=f"file://{self.schema_path}/",
                referrer=schema
            )
            validator = Draft202012Validator(schema, resolver=resolver)

            for error in validator.iter_errors(data):
                path = ".".join(str(p) for p in error.path) or "(root)"
                errors.append(f"{path}: {error.message}")
        except Exception as e:
            errors.append(f"Validation error: {str(e)}")

        return errors

    def get_properties(self, schema_name: str) -> dict[str, dict]:
        """
        Get property definitions from a schema.

        Args:
            schema_name: Schema name

        Returns:
            Dictionary of property names to their definitions
        """
        schema = self.get_schema(schema_name)
        if schema is None:
            return {}

        properties = schema.get("properties", {})

        # Also check allOf for inherited properties
        for item in schema.get("allOf", []):
            if "properties" in item:
                properties.update(item["properties"])

        return properties

    def get_required(self, schema_name: str) -> list[str]:
        """Get required fields for a schema."""
        schema = self.get_schema(schema_name)
        if schema is None:
            return []
        return schema.get("required", [])

    def get_defs(self, schema_name: str = "agency") -> dict:
        """Get $defs from the metaschema."""
        schema = self.get_schema(schema_name)
        if schema is None:
            return {}
        return schema.get("$defs", {})

    def describe(self, schema_name: str) -> dict:
        """
        Get a human-readable description of a schema.

        Returns:
            Dictionary with title, description, properties summary
        """
        schema = self.get_schema(schema_name)
        if schema is None:
            return {"error": f"Schema '{schema_name}' not found"}

        properties = self.get_properties(schema_name)
        required = self.get_required(schema_name)

        return {
            "name": schema_name,
            "title": schema.get("title", schema_name),
            "description": schema.get("description", ""),
            "type": schema.get("@type", []),
            "required": required,
            "properties": {
                name: {
                    "type": prop.get("type", "any"),
                    "description": prop.get("description", ""),
                    "required": name in required
                }
                for name, prop in properties.items()
            },
            "nim_meta": schema.get("$nim.meta", {})
        }

    def tree(self) -> dict:
        """
        Get a tree view of all schemas organized by category.

        Returns:
            Nested dictionary of categories -> schemas
        """
        if self._index is None:
            self._load_index()

        if self._index and "schemas" in self._index:
            return self._index["schemas"]

        # Fallback: build from loaded schemas
        tree = {"meta": {}, "reference": {}, "core": {}}
        for name, schema in self._schemas.items():
            meta = schema.get("$nim.meta", {})
            layer = meta.get("layer", {})
            component = layer.get("subcomponent", layer.get("component", "core"))

            if component in tree:
                tree[component][name] = {
                    "title": schema.get("title", name),
                    "description": schema.get("description", "")
                }

        return tree


# Module-level convenience instance
_default_manager: Optional[SchemaManager] = None


def get_schema_manager() -> SchemaManager:
    """Get the default schema manager instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = SchemaManager().load_all()
    return _default_manager
