"""FastAPI application entrypoint."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.auth.oidc import oidc_configured
from app.config import get_settings
from app.mcp import mcp_lifespan, mount_mcp

# `mcp_lifespan` runs the streamable-HTTP session manager's task group for the
# app's lifetime: the MCP endpoint is registered as a route rather than as a
# sub-application, so nothing else would ever hand it the lifespan protocol.
app = FastAPI(title="PromptRack", lifespan=mcp_lifespan)

_settings = get_settings()
if oidc_configured(_settings):
    # Authlib's Starlette client needs `request.session` to carry the OAuth
    # `state`/`nonce` across the redirect to the identity provider and back
    # (see app/auth/oidc.py) — added only when OIDC is configured, so an
    # install that never asked for SSO gets no extra cookie at all.
    app.add_middleware(SessionMiddleware, secret_key=_settings.session_secret, same_site="lax")

app.include_router(api_router, prefix="/api")

# `POST /mcp`, registered between the API router and the SPA catch-all below.
# It lives outside `/api` because the SPA has a settings page at the same path:
# the route is POST-only, so a browser's `GET /mcp` partial-matches here,
# Starlette keeps searching, and the catch-all serves the shell. This app
# authenticates itself (see `app.mcp.server.McpAuthMiddleware`) rather than
# through the FastAPI guards.
mount_mcp(app)

# The production image bakes the built SPA in here as `static/`
# (`frontend/dist`, copied by the Dockerfile) so one process on one port
# serves the API, the MCP endpoint and the frontend. In dev this directory
# never exists — `uv run uvicorn` serves the API only, and `npm run dev`'s
# vite dev server + proxy is what serves the SPA there — so the block below
# registers nothing and dev behaviour is unchanged.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if _STATIC_DIR.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve a built asset if the path names one, else `index.html`.

        vue-router runs in HTML5 history mode (`createWebHistory`), so a
        direct load or refresh of a client route like `/prompts/5` has to
        resolve server-side to the SPA shell, which then resolves the route
        itself. A concrete `/api/...` route registered above matches before
        this catch-all does, but a path *under* `/api` that matches no route
        at all (a typo, a disabled mock) would otherwise fall through to here
        too — excluded explicitly, so it still 404s as JSON via the handler
        below rather than 200ing the SPA shell.
        """
        if full_path == "api" or full_path.startswith("api/"):
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        candidate = (_STATIC_DIR / full_path).resolve()
        if candidate.is_file() and _STATIC_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")


# Every error the API returns carries a `message`, because that is the single
# field `frontend/src/api/client.ts` turns into an `ApiError`. FastAPI's own
# envelopes (`detail`) are replaced here rather than in each raise site, so a
# guard's 403 and a validation 422 read the same to the client.


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    del request
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(
        {"message": detail}, status_code=exc.status_code, headers=exc.headers or None
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 with a sentence in `message` and the raw errors alongside it.

    The first error is the message: a form has one field to fix at a time, and
    a client that wants the rest still gets `errors`.
    """
    del request
    errors = exc.errors()
    first = errors[0] if errors else None
    message = "Request failed validation."
    if first is not None:
        # `loc` is ("body", "password"); the field name is the useful half.
        field = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        reason = first.get("msg", message)
        message = f"{field}: {reason}" if field else str(reason)
    return JSONResponse(
        {"message": message, "errors": jsonable_errors(errors)}, status_code=422
    )


def jsonable_errors(errors: list[dict[str, object]]) -> list[dict[str, object]]:
    """Pydantic's error dicts can carry a `ctx` holding the original exception,
    which `json.dumps` refuses. Only the JSON-safe keys survive.
    """
    return [
        {key: value for key, value in error.items() if key in ("type", "loc", "msg", "input")}
        for error in errors
    ]
