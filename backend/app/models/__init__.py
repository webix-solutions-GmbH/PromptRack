"""SQLAlchemy models.

Importing this package registers every table on `Base.metadata`, which is what
Alembic's `env.py` (it imports `app.models.base`, and that imports this package
first) autogenerates against.
"""

from app.models.auth import ApiToken, Session, User, UserRole
from app.models.base import Base
from app.models.customers import Customer
from app.models.endpoints import Endpoint, EndpointModel, EndpointModelSource
from app.models.prompts import Prompt, PromptKind, PromptVersion
from app.models.runs import (
    Rating,
    ResultStatus,
    Run,
    RunResult,
    RunStatus,
    StoppedReason,
)
from app.models.test_cases import TestCase, TestCaseToolset, TestGroup
from app.models.toolsets import (
    Tool,
    ToolChoice,
    ToolMode,
    Toolset,
    ToolsetKind,
    ToolSource,
)

__all__ = [
    "ApiToken",
    "Base",
    "Customer",
    "Endpoint",
    "EndpointModel",
    "EndpointModelSource",
    "Prompt",
    "PromptKind",
    "PromptVersion",
    "Rating",
    "ResultStatus",
    "Run",
    "RunResult",
    "RunStatus",
    "Session",
    "StoppedReason",
    "TestCase",
    "TestCaseToolset",
    "TestGroup",
    "Tool",
    "ToolChoice",
    "ToolMode",
    "ToolSource",
    "Toolset",
    "ToolsetKind",
    "User",
    "UserRole",
]
