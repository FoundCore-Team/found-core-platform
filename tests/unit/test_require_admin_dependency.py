import pytest
from fastapi import HTTPException

from app.api.v1.dependencies.auth import require_admin
from tests.fixtures.factories import make_user_entity


async def test_an_admin_passes_and_is_returned():
    admin = make_user_entity(id=1, is_admin=True)

    assert await require_admin(current_user=admin) is admin


async def test_a_regular_user_gets_403_with_a_machine_readable_code():
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(current_user=make_user_entity(id=2, is_admin=False))

    assert exc_info.value.status_code == 403
    # A dict, unlike the ban's 403 whose detail is a plain string: a client tells the two apart without parsing text.
    assert exc_info.value.detail == {"code": "admin_required", "message": "Admin access required."}
