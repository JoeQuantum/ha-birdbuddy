"""Keep runtime pins, compatibility lanes and the advertised floor coherent."""

from __future__ import annotations

from importlib.metadata import requires, version
import json
from pathlib import Path
import sys
from typing import Any, cast

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import yaml

ROOT = Path(__file__).resolve().parent.parent
TARGETS = cast(
    dict[str, dict[str, str]],
    json.loads((ROOT / "tests/ha_targets.json").read_text()),
)


def _requirements(path: str) -> dict[str, Requirement]:
    """Read direct pins without following another requirements file."""
    parsed: dict[str, Requirement] = {}
    for line in (ROOT / path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-r ")):
            continue
        requirement = Requirement(line)
        parsed[canonicalize_name(requirement.name)] = requirement
    return parsed


def test_runtime_requirements_match_manifest() -> None:
    """Changing the client must update the manifest and test dependency source."""
    manifest = json.loads(
        (ROOT / "custom_components/birdbuddy/manifest.json").read_text()
    )
    expected = {
        canonicalize_name(requirement.name): str(requirement)
        for value in manifest["requirements"]
        if (requirement := Requirement(value))
    }
    actual = {
        name: str(requirement)
        for name, requirement in _requirements("requirements.txt").items()
    }
    assert actual == expected
    assert "homeassistant" not in actual


def test_full_suite_uses_an_exact_harness_runtime_pair() -> None:
    """Every full-suite lane uses the HA release its harness requires."""
    harness_version = version("pytest-homeassistant-custom-component")
    matches = [
        TARGETS[name]
        for name in ("minimum", "modern")
        if TARGETS[name]["harness"] == harness_version
    ]
    assert len(matches) == 1, f"Unrecorded test harness version: {harness_version}"
    target = matches[0]
    assert version("homeassistant") == target["homeassistant"]
    harness_dependencies = [
        Requirement(value)
        for value in requires("pytest-homeassistant-custom-component") or []
    ]
    assert any(
        canonicalize_name(dependency.name) == "homeassistant"
        and str(dependency.specifier) == f"=={target['homeassistant']}"
        for dependency in harness_dependencies
    )
    assert f"{sys.version_info.major}.{sys.version_info.minor}" == ".".join(
        target["python"].split(".")[:2]
    )


def test_declared_minimum_and_development_python_match_targets() -> None:
    """A modern test lane must not silently raise the supported minimum."""
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["homeassistant"] == TARGETS["minimum"]["homeassistant"]
    assert (ROOT / ".python-version").read_text().strip() == TARGETS["modern"]["python"]
    for name in ("minimum", "modern"):
        target = TARGETS[name]
        pin = _requirements(target["requirements"])[
            "pytest-homeassistant-custom-component"
        ]
        assert str(pin.specifier) == f"=={target['harness']}"
    current = TARGETS["current_smoke"]
    smoke = _requirements(current["requirements"])
    assert str(smoke["homeassistant"].specifier) == f"=={current['homeassistant']}"
    assert "pytest-homeassistant-custom-component" not in smoke


def test_ci_matrix_and_smoke_lane_match_declared_targets() -> None:
    """CI exercises the pins documented in the compatibility target table."""
    # BaseLoader preserves YAML's literal 'on' key instead of parsing it as bool.
    workflow = cast(
        dict[str, Any],
        yaml.load(
            (ROOT / ".github/workflows/pythonpackage.yaml").read_text(),
            Loader=yaml.BaseLoader,
        ),
    )
    assert "pull_request" in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]["include"]
    assert {row["target"] for row in matrix} == {"minimum", "modern"}
    for row in matrix:
        target = TARGETS[row["target"]]
        assert row["ha"] == target["homeassistant"]
        assert row["python"] == target["python"]
        assert row["requirements"] == target["requirements"]
    smoke_job = workflow["jobs"]["current-runtime-smoke"]
    assert TARGETS["current_smoke"]["homeassistant"] in smoke_job["name"]
    assert any(
        "REQUIREMENTS=requirements.smoke.txt" in step.get("run", "")
        for step in smoke_job["steps"]
    )
