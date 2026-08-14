"""API router aggregation. Domain routers are added here as they land."""

from fastapi import APIRouter

from app.api.customers import router as customers_router
from app.api.machines import router as machines_router
from app.api.mocks import router as mocks_router
from app.api.prompts import router as prompts_router
from app.api.results import router as results_matrix_router
from app.api.runs import results_router
from app.api.runs import router as runs_router
from app.api.test_cases import router as test_cases_router
from app.api.test_groups import router as test_groups_router
from app.api.tokens import router as tokens_router
from app.api.toolsets import router as toolsets_router
from app.api.version import router as version_router
from app.auth.oidc import oidc_configured
from app.auth.oidc import router as oidc_router
from app.auth.router import router as auth_router

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


router.include_router(version_router)
router.include_router(auth_router)
router.include_router(tokens_router)
router.include_router(customers_router)
router.include_router(machines_router)
router.include_router(prompts_router)
router.include_router(toolsets_router)
router.include_router(test_groups_router)
router.include_router(test_cases_router)
router.include_router(runs_router)
# Strictly before `results_router`: routes match in registration order, and
# `/results/{result_id}` would otherwise swallow `/results/matrix` and answer a
# 422 for "matrix is not an integer".
router.include_router(results_matrix_router)
router.include_router(results_router)
# Always mounted (unlike the OIDC router below): `app.api.mocks` gates every
# route itself with `mocks_enabled()`, returning a 404 rather than the route
# not existing, which is what keeps a production deployment indistinguishable
# from one built without the mocks at all.
router.include_router(mocks_router)

# Absent OIDC config, this router simply is not mounted — no
# `/api/auth/oidc/*` surface exists at all rather than 404ing per-route.
if oidc_configured():
    router.include_router(oidc_router)
