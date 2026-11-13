"""Build or verify an offline release ZIP with per-file SHA256 integrity metadata.

This does not publish a release or modify an installed integration. The archive
contains custom_components/birdbuddy and documentation for a manual installation.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

PREFIX = "custom_components/birdbuddy/"
INTEGRITY = "birdbuddy-integrity.json"
REQUIRED = {
    PREFIX + path
    for path in (
        "__init__.py",
        "manifest.json",
        "coordinator.py",
        "config_flow.py",
        "intakestore.py",
        "events.py",
        "postcard_policy.py",
        "services.yaml",
        "strings.json",
    )
}
MAX_ARCHIVE_BYTES = 20_000_000


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _validate_files(files: dict[str, bytes]) -> None:
    if not REQUIRED.issubset(files):
        raise ValueError(
            "Release is missing required integration files; reinstall the complete package"
        )
    for name, content in files.items():
        if not _safe_name(name):
            raise ValueError("Archive contains an unsafe path")
        if name.startswith(PREFIX) and not content.strip():
            raise ValueError(
                f"Empty integration file: {name}; restore or reinstall the complete package"
            )
        if name.endswith(".py"):
            compile(content, name, "exec")
        if name.endswith(".json"):
            json.loads(content)
    manifest = json.loads(files[PREFIX + "manifest.json"])
    if manifest.get("domain") != "birdbuddy" or not manifest.get("version"):
        raise ValueError("Invalid integration manifest")
    pins = [
        value
        for value in manifest.get("requirements", [])
        if value.startswith("pybirdbuddy@")
    ]
    if len(pins) != 1 or not re.fullmatch(
        r"pybirdbuddy@git\+https://github\.com/JoeQuantum/pybirdbuddy@[a-f0-9]{40}",
        pins[0],
    ):
        raise ValueError("Release requires exactly one immutable pybirdbuddy revision")


def build(root: Path, output: Path) -> dict:
    files = {}
    for directory in (
        root / "custom_components" / "birdbuddy",
        root / "docs",
        root / "blueprints",
        root / "examples",
    ):
        for path in sorted(directory.rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
            ):
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    for name in ("archive_postcard.py", "ha_runtime_smoke.py", "package_release.py"):
        files[f"scripts/{name}"] = (root / "scripts" / name).read_bytes()
    for name in ("README.md", "DEVELOPMENT.md", "LICENSE", "hacs.json"):
        if (path := root / name).is_file():
            files[name] = path.read_bytes()
    _validate_files(files)
    integrity = {
        "schema_version": 1,
        "files": {name: sha256(data).hexdigest() for name, data in files.items()},
    }
    files[INTEGRITY] = json.dumps(integrity, indent=2, sort_keys=True).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return verify(output)


def verify(path: Path) -> dict:
    with ZipFile(path) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        if len(set(names)) != len(names) or any(not _safe_name(name) for name in names):
            raise ValueError("Duplicate or unsafe archive paths")
        if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
            raise ValueError("Archive exceeds the inspection budget")
        files = {name: archive.read(name) for name in names}
    integrity = json.loads(files.pop(INTEGRITY))
    if integrity.get("schema_version") != 1:
        raise ValueError("Unsupported integrity manifest")
    expected = integrity.get("files")
    actual = {name: sha256(data).hexdigest() for name, data in files.items()}
    if expected != actual:
        raise ValueError("Package checksum mismatch; reinstall the complete package")
    _validate_files(files)
    manifest = json.loads(files[PREFIX + "manifest.json"])
    return {
        "version": manifest["version"],
        "files": len(files),
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "client": manifest["requirements"][-1],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--build-from", type=Path)
    args = parser.parse_args()
    result = (
        build(args.build_from, args.archive)
        if args.build_from
        else verify(args.archive)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
