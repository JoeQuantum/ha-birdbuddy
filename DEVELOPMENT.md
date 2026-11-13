# Development and compatibility checks

Bird Buddy Plus supports Home Assistant **2025.1.4 or newer**. The minimum
and modern test lanes run the complete integration suite with an exactly
matched Home Assistant test harness. A separate current-runtime smoke check
tests imports and the configuration schema; it does not establish full runtime
or hardware compatibility.

| Check | Home Assistant | Python | Test harness |
| --- | --- | --- | --- |
| Minimum full suite | 2025.1.4 | 3.12.13 | 0.13.205 |
| Modern full suite | 2026.7.1 | 3.14.4 | 0.13.345 |
| Current import/config smoke | 2026.11.1 | 3.14.4 | Not used |

`tests/ha_targets.json` records these targets. Tests check the requirements,
CI matrix, HACS minimum and development Python version against that table.
A newer test lane does not automatically raise the supported minimum.

## Local checks

Use the Python version in `.python-version` for the default environment:

```sh
make deps PYTHON=python3.14
make check
```

`make check` runs correctness-focused Ruff rules, a formatting check, Pyright
and pytest. `make format` formats source and tests explicitly. Pyright uses
declared third-party types instead of inferring types from untyped dependency
implementation code; the older Bird Buddy client contains invalid `any`
annotations. The HA entity multiple-inheritance override exemption matches
the upstream integration. Optional values and test doubles still require
explicit checks or type annotations.

Run the supported minimum in a separate environment:

```sh
make deps PYTHON=python3.12 VENV=.venv-minimum REQUIREMENTS=requirements.test.txt
make test VENV=.venv-minimum
```

Run the current runtime smoke check separately, without a test harness:

```sh
make deps PYTHON=python3.14 VENV=.venv-current-smoke REQUIREMENTS=requirements.smoke.txt
make smoke VENV=.venv-current-smoke
```

The smoke script verifies the installed HA version, imports every integration
module and validates an empty YAML configuration. It loads Home Assistant
before integration dependencies so recent HA dependency aliases are applied.
It does not start Home Assistant, authenticate, access an account or perform
Bird Buddy operations.

## Updating dependencies

Runtime requirements must match `custom_components/birdbuddy/manifest.json`
and `requirements.txt`. Home Assistant is supplied by the user's installation;
it is not an integration runtime dependency. Each full-suite requirements file
pins a test harness that owns the exact HA and pytest versions. Avoid adding
conflicting pytest pins or bumping the Bird Buddy client independently of its
integration API migration.

Update compatibility targets deliberately, including their requirement files,
`tests/ha_targets.json`, the CI matrix and this table. Only change the HACS
minimum when the oldest supported lane changes. Development-tool pins and
GitHub Actions updates are separate from that decision.

As of this baseline, harness `0.13.375` requires HA `2026.10.4`. A test-only
override to HA `2026.11.1` failed during test collection because the harness
imports the removed private helper `logger._clear_logger_overwrites`.
That combination is not a passing full-suite lane, and the harness was not
patched to hide the incompatibility. Keep the current smoke check until a
compatible harness is available.
