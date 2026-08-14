"""`GET /api/version` — build identity, unauthenticated like `/api/health`.

`version` comes from the installed `promptrack-backend` package metadata
(what `uv sync` records), falling back to reading `pyproject.toml`'s
`version` field directly when the package metadata is unavailable (e.g. run
from a checkout that was never `uv sync`ed as a package), and finally to
`"0.0.0"` — this endpoint must never raise. `commit` is whatever
`PROMPTRACK_COMMIT` was baked in at image build time (see the Dockerfile's
backend stage), `null` in dev where nothing sets it.
"""

from __future__ import annotations

import os
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["version"])

_PACKAGE_NAME = "promptrack-backend"
_PYPROJECT_PATH = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"


class VersionView(BaseModel):
    version: str
    commit: str | None


def _resolve_version() -> str:
    try:
        return _package_version(_PACKAGE_NAME)
    except PackageNotFoundError:
        pass
    try:
        data = tomllib.loads(_PYPROJECT_PATH.read_text())
        value = data.get("project", {}).get("version")
        if isinstance(value, str) and value:
            return value
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return "0.0.0"


@router.get("/version")
async def get_version() -> VersionView:
    commit = os.environ.get("PROMPTRACK_COMMIT") or None
    return VersionView(version=_resolve_version(), commit=commit)
