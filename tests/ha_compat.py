"""Select one official or legacy HA harness without patching either package.

Older supported runtimes retain their exactly matched legacy harness. Current
HA uses the package maintained in home-assistant/core. Never install both.
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version


def _installed_harness() -> str:
    installed = []
    for name in ("pytest-homeassistant", "pytest-homeassistant-custom-component"):
        try:
            version(name)
        except PackageNotFoundError:
            continue
        installed.append(name)
    if len(installed) != 1:
        raise RuntimeError(f"Install exactly one HA test harness; found {installed}")
    return installed[0]


HARNESS_DISTRIBUTION = _installed_harness()
HARNESS_MODULE = HARNESS_DISTRIBUTION.replace("-", "_")
PYTEST_PLUGINS = (
    ("pytest_homeassistant_custom_component",)
    if HARNESS_DISTRIBUTION == "pytest-homeassistant-custom-component"
    else ()
)

common = import_module(f"{HARNESS_MODULE}.common")
