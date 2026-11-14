"""Detect damaged/restored artifacts before attributing failures to credentials."""

from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.package_release import build, verify, INTEGRITY, PREFIX

ROOT = Path(__file__).resolve().parents[1]


def test_release_build_is_reproducible_and_pinned(tmp_path):
    first = build(ROOT, tmp_path / "first.zip")
    second = build(ROOT, tmp_path / "second.zip")
    assert first == second
    assert first["client"].startswith("pybirdbuddy@git+")
    assert verify(tmp_path / "first.zip") == first


@pytest.mark.parametrize("mode", ["empty", "missing", "unsafe"])
def test_damaged_release_is_not_an_authentication_problem(tmp_path, mode):
    path = tmp_path / "package.zip"
    build(ROOT, path)
    with ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if mode == "empty":
        files[PREFIX + "coordinator.py"] = b""
    elif mode == "missing":
        files.pop(PREFIX + "coordinator.py")
    else:
        files["../outside.py"] = b"pass"
    with ZipFile(path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    with pytest.raises(ValueError, match="checksum|unsafe"):
        verify(path)


def test_empty_file_rejected_even_if_checksum_is_recomputed(tmp_path):
    import json
    from hashlib import sha256

    path = tmp_path / "package.zip"
    build(ROOT, path)
    with ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files[PREFIX + "coordinator.py"] = b""
    data = json.loads(files[INTEGRITY])
    data["files"][PREFIX + "coordinator.py"] = sha256(b"").hexdigest()
    files[INTEGRITY] = json.dumps(data).encode()
    with ZipFile(path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    with pytest.raises(ValueError, match="Empty integration file"):
        verify(path)
