"""A live MCP client: list a server's tools and call one, over streamable HTTP.

Transport is streamable HTTP only, via the official SDK (`mcp`) rather than a
hand-rolled JSON-RPC layer. A toolset is configured exactly like an endpoint — a
URL plus optional auth headers — so the deployed container needs nothing baked
in: an Odoo or websearch MCP server runs as its own container on the same
network and is reached by URL.

Connections are not pooled: one is opened per operation and always closed
again, which keeps lifecycle and failure handling trivial for a sequential,
low-frequency caller (a discovery pass, or later a run's tool loop).

Kept free of the database and of `app.repos` on purpose, the same split
`app.services.discovery` makes for endpoint probing — this is a pure network
call plus response parsing.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import Client, Implementation
from mcp.client.streamable_http import streamable_http_client

#: Generous: an MCP server's `tools/list` may itself call out to something
#: slow.
DEFAULT_TIMEOUT_S = 60.0

_CLIENT_INFO = Implementation(name="promptrack", version="0.3.0")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class McpClientError(Exception):
    """A connection- or protocol-level failure talking to an MCP server."""


@dataclass(frozen=True)
class McpToolDescriptor:
    """One tool as the server currently describes it."""

    name: str
    description: str | None
    #: The tool's `inputSchema`, serialized — already a JSON Schema object.
    parameters_json: str


def parse_mcp_headers(raw: str | None) -> dict[str, str] | None:
    """Parses a toolset's stored `mcp_headers` column.

    Anything that is not a JSON object of strings degrades to `None` rather
    than raising — a malformed stored value should not crash discovery, it
    should just connect without extra headers.
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    headers = {key: value for key, value in parsed.items() if isinstance(value, str)}
    return headers or None


def _unwrap(exc: Exception) -> BaseException:
    """Descends into an `ExceptionGroup` to the first leaf exception.

    The SDK's transport runs inside an `anyio` task group, so a transport
    failure (a refused connection, a timeout) surfaces wrapped in an
    `ExceptionGroup` rather than as the underlying `httpx2` error directly.
    """
    seen: BaseException = exc
    while isinstance(seen, BaseExceptionGroup) and seen.exceptions:
        seen = seen.exceptions[0]
    return seen


def _describe_error(exc: Exception, url: str) -> str:
    leaf = _unwrap(exc)
    if isinstance(leaf, httpx2.TimeoutException):
        return f"Connection timed out. ({url})"
    if isinstance(leaf, httpx2.ConnectError):
        reason = str(leaf.__cause__) if leaf.__cause__ else str(leaf)
        detail = f": {reason}" if reason else ""
        return f"Connection failed{detail} — is the server running? ({url})"
    message = str(leaf) or "Unknown MCP error."
    return f"{message} ({url})"


async def list_mcp_tools(
    url: str, headers_json: str | None, *, timeout: float = DEFAULT_TIMEOUT_S
) -> list[McpToolDescriptor]:
    """Everything a live server currently advertises under `tools/list`.

    Opens one connection, lists tools, and always closes again — no session
    survives past this call, matching the "per-operation connection" the plan
    asks for. Raises :class:`McpClientError` for anything that keeps this from
    completing; a successful-but-empty tool list is not an error.
    """
    cleaned = url.strip()
    if not _URL_RE.match(cleaned):
        raise McpClientError(f'"{cleaned}" is not an http(s) MCP endpoint.')

    request_headers = parse_mcp_headers(headers_json)
    try:
        async with httpx2.AsyncClient(
            headers=request_headers, timeout=httpx2.Timeout(timeout)
        ) as http_client:
            transport = streamable_http_client(cleaned, http_client=http_client)
            async with Client(transport, client_info=_CLIENT_INFO) as client:
                result = await client.list_tools()
    except McpClientError:
        raise
    except Exception as exc:  # noqa: BLE001 - translated into one error type
        # Deliberately not `BaseException`: a `CancelledError` (client
        # disconnect, request timeout at a higher layer) must keep propagating
        # as cancellation, not get relabeled as a server-side connection error.
        raise McpClientError(_describe_error(exc, cleaned)) from exc

    return [
        McpToolDescriptor(
            name=tool.name,
            description=tool.description,
            parameters_json=json.dumps(tool.input_schema or {"type": "object", "properties": {}}),
        )
        for tool in result.tools
    ]


@dataclass(frozen=True)
class McpCallResult:
    """What one `tools/call` produced, as the model will see it."""

    #: Flattened text of the result's content blocks.
    content: str
    is_error: bool


def flatten_content(blocks: object) -> str:
    """Flattens MCP content blocks into one string.

    A model only ever sees a tool message as text, so a non-text block is
    *described* rather than dropped — silently losing an image would look to
    the model like the tool returned nothing at all.
    """
    if not isinstance(blocks, list):
        return ""

    parts: list[str] = []
    for block in blocks:
        kind = getattr(block, "type", None)
        if kind == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        elif isinstance(kind, str):
            parts.append(f"[{kind} content omitted]")
    return "\n".join(parts)


async def call_mcp_tool(
    url: str,
    headers_json: str | None,
    name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> McpCallResult:
    """Really executes one tool against a live server.

    Same per-operation connection as :func:`list_mcp_tools`, and the same
    translation of every transport failure into :class:`McpClientError` — which
    the tool loop turns into the tool's *output* rather than a failed result
    row, because that is what a real agent would see.

    A tool that reports an error with no text still has to say something, or
    the model is left staring at an empty message.
    """
    cleaned = url.strip()
    if not _URL_RE.match(cleaned):
        raise McpClientError(f'"{cleaned}" is not an http(s) MCP endpoint.')

    request_headers = parse_mcp_headers(headers_json)
    try:
        async with httpx2.AsyncClient(
            headers=request_headers, timeout=httpx2.Timeout(timeout)
        ) as http_client:
            transport = streamable_http_client(cleaned, http_client=http_client)
            async with Client(transport, client_info=_CLIENT_INFO) as client:
                result = await client.call_tool(name, dict(arguments or {}))
    except McpClientError:
        raise
    except Exception as exc:  # noqa: BLE001 - translated into one error type
        raise McpClientError(_describe_error(exc, cleaned)) from exc

    is_error = result.is_error is True
    content = flatten_content(result.content)
    if not content:
        content = json.dumps(
            {"error": f'Tool "{name}" reported an error with no detail.'}
            if is_error
            else {"result": "ok", "detail": "The tool returned no content."}
        )
    return McpCallResult(content=content, is_error=is_error)
