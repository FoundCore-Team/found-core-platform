from fastapi import APIRouter

from .routers import (
    admin_actions,
    admin_profiles,
    admin_reports,
    admin_tags,
    admin_users,
    auth,
    contacts,
    feed,
    profiles,
    reports,
    taxonomy,
)

api_v1_router = APIRouter(prefix="/v1")
router_list = [
    auth,
    taxonomy,
    profiles,
    feed,
    contacts,
    reports,
    admin_users,
    admin_profiles,
    admin_reports,
    admin_tags,
    admin_actions,
]

for router in router_list:
    api_v1_router.include_router(router.router)
