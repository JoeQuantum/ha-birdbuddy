"""Fixture loading, privacy checks and selected-response schema validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_ROOT = Path(__file__).parent


def load_manifest() -> dict[str, Any]:
    """Return independent metadata for the committed fixture corpus."""
    return json.loads((_ROOT / "manifest.json").read_text())


def load_fixture(name: str) -> Any:
    """Load an allowlisted fixture, freshly parsed to prevent shared mutations."""
    if name not in load_manifest()["fixtures"]:
        raise ValueError(f"Unknown fixture: {name!r}")
    return json.loads((_ROOT / "data" / name).read_text())


def privacy_errors(value: Any, path: str = "$") -> list[str]:
    """Flag prohibited credentials and non-placeholder URLs/emails.

    This is a mechanical guard for an already reviewed corpus, not a guarantee
    that arbitrary raw payloads contain no personal information.
    """
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.lower() in {
                "password",
                "accesstoken",
                "refreshtoken",
                "reporttoken",
                "authorization",
                "credential",
                "token",
            } and child not in (None, "REDACTED"):
                errors.append(f"{child_path}: credential-like value")
            if key.lower().endswith("email") and isinstance(child, str):
                if not child.endswith("@example.invalid"):
                    errors.append(f"{child_path}: non-placeholder email")
            errors.extend(privacy_errors(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(privacy_errors(child, f"{path}[{index}]"))
    elif isinstance(value, str) and value.startswith(("http://", "https://", "wss://")):
        url = urlsplit(value)
        if url.hostname != "example.invalid":
            errors.append(f"{path}: non-placeholder URL host")
        if url.username or url.password or url.fragment:
            errors.append(f"{path}: URL contains credentials or fragment")
    return errors


def validate_fixture_shape(name: str) -> None:
    """Validate selected JSON fields against a projection of live introspection.

    Missing fields are permitted: GraphQL responses contain only selections.
    Present null values must respect nullability. This checks data structure,
    not query selection completeness, authorization or live backend behavior.
    Intentionally malformed fixtures are expected to raise ValueError.
    """
    metadata = load_manifest()["fixtures"][name]
    snapshot = json.loads((_ROOT / "schema_shapes.json").read_text())
    types = snapshot["types"]

    def check(item: Any, ref: dict[str, Any], path: str) -> None:
        kind = ref["kind"]
        if kind == "NON_NULL":
            if item is None:
                raise ValueError(f"{path}: unexpected null")
            check(item, ref["ofType"], path)
            return
        if item is None:
            return
        if kind == "LIST":
            if not isinstance(item, list):
                raise ValueError(f"{path}: expected list")
            for index, child in enumerate(item):
                check(child, ref["ofType"], f"{path}[{index}]")
            return
        type_name = ref["name"]
        if kind == "SCALAR":
            valid = True
            if type_name in {"String", "ID", "DateTime"}:
                valid = isinstance(item, str)
            elif type_name == "Boolean":
                valid = isinstance(item, bool)
            elif type_name == "Int":
                valid = isinstance(item, int) and not isinstance(item, bool)
            elif type_name == "Float":
                valid = isinstance(item, (int, float)) and not isinstance(item, bool)
            if not valid:
                raise ValueError(f"{path}: invalid {type_name}")
            return
        contract = types[type_name]
        if kind == "ENUM":
            if item not in contract["enum_values"]:
                raise ValueError(f"{path}: unknown {type_name} value")
            return
        if not isinstance(item, dict):
            raise ValueError(f"{path}: expected object")
        actual = item.get("__typename", type_name)
        if actual != type_name:
            if actual not in contract["possible_types"]:
                raise ValueError(f"{path}: unexpected type {actual}")
            contract = types[actual]
        for key, child in item.items():
            if key == "__typename":
                continue
            if key not in contract["fields"]:
                raise ValueError(f"{path}.{key}: field absent from {actual}")
            check(child, contract["fields"][key], f"{path}.{key}")

    root = metadata["root_type"]
    ref = {"kind": types[root]["kind"], "name": root}
    data = load_fixture(name)
    if metadata.get("root_is_list"):
        ref = {"kind": "LIST", "ofType": ref}
    check(data, ref, "$")
