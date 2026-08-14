"""`app.api.version._resolve_version` — the fallback chain only.

No database, no app instantiation: this pins down installed-metadata ->
pyproject.toml -> "0.0.0", each step exercised by monkeypatching the
previous one away, never by uninstalling the real package.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import app.api.version as version_module


def test_resolve_version_uses_installed_package_metadata(monkeypatch):
    monkeypatch.setattr(version_module, "_package_version", lambda name: "9.9.9")
    assert version_module._resolve_version() == "9.9.9"


def test_resolve_version_falls_back_to_pyproject_toml(monkeypatch, tmp_path):
    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version_module, "_package_version", _raise)

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "promptrack-backend"\nversion = "1.2.3"\n')
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", pyproject)

    assert version_module._resolve_version() == "1.2.3"


def test_resolve_version_falls_back_to_zero_when_pyproject_is_missing(monkeypatch, tmp_path):
    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version_module, "_package_version", _raise)
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", tmp_path / "does-not-exist.toml")

    assert version_module._resolve_version() == "0.0.0"


def test_resolve_version_falls_back_to_zero_when_pyproject_has_no_version(monkeypatch, tmp_path):
    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version_module, "_package_version", _raise)

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "promptrack-backend"\n')
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", pyproject)

    assert version_module._resolve_version() == "0.0.0"


def test_resolve_version_falls_back_to_zero_on_malformed_pyproject(monkeypatch, tmp_path):
    def _raise(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version_module, "_package_version", _raise)

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("not valid toml [[[")
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", pyproject)

    assert version_module._resolve_version() == "0.0.0"
