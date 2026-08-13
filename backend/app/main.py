"""FastAPI application entrypoint."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.auth.oidc import oidc_configured
from app.config import get_settings

app = FastAPI(title="PromptRack")

_settings = get_settings()
if oidc_configured(_settings):
    # Authlib's Starlette client needs `request.session` to carry the OAuth
    # `state`/`nonce` across the redirect to the identity provider and back
    # (see app/auth/oidc.py) — added only when OIDC is configured, so an
    # install that never asked for SSO gets no extra cookie at all.
    app.add_middleware(SessionMiddleware, secret_key=_settings.session_secret, same_site="lax")

app.include_router(api_router, prefix="/api")


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
