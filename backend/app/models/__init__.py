"""SQLAlchemy models.

Importing this package registers every table on `Base.metadata`, which is what
Alembic's `env.py` (it imports `app.models.base`, and that imports this package
first) autogenerates against.
"""

from app.models.auth import ApiToken, Session, User, UserInvite, UserRole
from app.models.base import Base
from app.models.customers import Customer
from app.models.endpoints import Endpoint, EndpointModel, EndpointModelSource, EndpointPlatform
from app.models.prompts import Prompt, PromptKind, PromptVersion
from app.models.runs import (
    RatedVia,
    Rating,
    ResultStatus,
    Run,
    RunResult,
    RunStatus,
    StoppedReason,
)
from app.models.test_cases import TestCase, TestCaseToolset, TestGroup
from app.models.toolsets import (
    DOCUMENT_SEARCH_CONFIG,
    DOCUMENT_TSV_EXPRESSION,
    Document,
    Tool,
    ToolChoice,
    ToolMode,
    Toolset,
    ToolsetKind,
    ToolSource,
)

__all__ = [
    "DOCUMENT_SEARCH_CONFIG",
    "DOCUMENT_TSV_EXPRESSION",
    "ApiToken",
    "Base",
    "Customer",
    "Document",
    "Endpoint",
    "EndpointModel",
    "EndpointModelSource",
    "EndpointPlatform",
    "Prompt",
    "PromptKind",
    "PromptVersion",
    "RatedVia",
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
    "UserInvite",
    "UserRole",
]
