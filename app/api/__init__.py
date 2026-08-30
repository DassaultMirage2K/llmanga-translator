"""API package — aggregates all routers."""

from fastapi import APIRouter, Request


def get_state(request: Request):
    """Return the shared AppState (db/storage/ws/jobs) built at startup."""
    return request.app.state.app_state


router = APIRouter()

# Import sub-routers and mount them so their routes register on `router`.
from . import settings  # noqa: E402,F401
from . import works  # noqa: E402,F401
from . import translate  # noqa: E402,F401

router.include_router(settings.router)
router.include_router(works.router)
router.include_router(translate.router)
