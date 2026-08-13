"""API router aggregation. Domain routers are added here as they land."""

from fastapi import APIRouter

from app.api.customers import router as customers_router
from app.api.machines import router as machines_router
from app.api.prompts import router as prompts_router
from app.api.test_cases import router as test_cases_router
from app.api.test_groups import router as test_groups_router
from app.api.tokens import router as tokens_router
from app.api.toolsets import router as toolsets_router
from app.auth.oidc import oidc_configured
from app.auth.oidc import router as oidc_router
from app.auth.router import router as auth_router

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


router.include_router(auth_router)
router.include_router(tokens_router)
router.include_router(customers_router)
router.include_router(machines_router)
router.include_router(prompts_router)
router.include_router(toolsets_router)
router.include_router(test_groups_router)
router.include_router(test_cases_router)

# Absent OIDC config, this router simply is not mounted — no
# `/api/auth/oidc/*` surface exists at all rather than 404ing per-route.
if oidc_configured():
    router.include_router(oidc_router)
