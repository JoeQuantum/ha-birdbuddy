"""Check exact HA runtime imports without claiming a matched test harness.

Install requirements.smoke.txt in an isolated environment before running this
script. It does not log in, start Home Assistant, poll Bird Buddy or mutate data.
"""

from __future__ import annotations

import argparse
import importlib
from importlib.metadata import version
from pathlib import Path
import pkgutil
import sys


def main() -> None:
    """Import every integration module against the explicitly selected HA."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-ha", required=True)
    args = parser.parse_args()
    installed = version("homeassistant")
    if installed != args.expected_ha:
        raise SystemExit(f"Expected HA {args.expected_ha}; installed {installed}")

    # Executing a script places scripts/, not the repository, on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    # HA initializes dependency aliases on recent releases. Match that import
    # order before a custom integration imports voluptuous or HTTP helpers.
    importlib.import_module("homeassistant")
    component = importlib.import_module("custom_components.birdbuddy")
    modules = [component.__name__]
    for item in pkgutil.iter_modules(component.__path__):
        module_name = f"{component.__name__}.{item.name}"
        importlib.import_module(module_name)
        modules.append(module_name)
    component.CONFIG_SCHEMA({})
    print(
        f"HA {installed}: imported {len(modules)} Bird Buddy modules and "
        "validated the empty YAML configuration. "
        "Import smoke only; no account access or full harness validation."
    )


if __name__ == "__main__":
    main()
