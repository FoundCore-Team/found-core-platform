import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app.infrastructure.helpers import db_helper
from app.infrastructure.models import ProfileModel, ProfileViewModel
from app.infrastructure.repositories import SqlAlchemyProfileViewRepository
from tests.fixtures.feed_data import add_profile, add_user
from tests.fixtures.taxonomy_data import TaxonomyIds

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def two_profiles(session, taxonomy_data: TaxonomyIds) -> tuple[int, int]:
    first = await add_profile(
        session, await add_user(session, 1), taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id
    )
    second = await add_profile(
        session, await add_user(session, 2), taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id
    )
    return first, second


async def view_rows(session) -> list[tuple[int, int, datetime]]:
    result = await session.execute(
        select(ProfileViewModel.viewer_profile_id, ProfileViewModel.viewed_profile_id, ProfileViewModel.viewed_at)
    )
    return [tuple(row) for row in result.all()]


async def test_a_view_is_stored(session, two_profiles):
    viewer, viewed = two_profiles

    await SqlAlchemyProfileViewRepository(session).add(viewer, viewed, NOW)
    await session.commit()

    assert await view_rows(session) == [(viewer, viewed, NOW)]


async def test_a_repeat_view_neither_fails_nor_changes_the_first_timestamp(session, two_profiles):
    viewer, viewed = two_profiles
    repository = SqlAlchemyProfileViewRepository(session)

    await repository.add(viewer, viewed, NOW)
    await repository.add(viewer, viewed, NOW + timedelta(days=1))
    await session.commit()

    assert await view_rows(session) == [(viewer, viewed, NOW)]


async def test_a_view_is_directional(session, two_profiles):
    first, second = two_profiles
    repository = SqlAlchemyProfileViewRepository(session)

    await repository.add(first, second, NOW)
    await repository.add(second, first, NOW)
    await session.commit()

    assert {(viewer, viewed) for viewer, viewed, _ in await view_rows(session)} == {(first, second), (second, first)}


async def test_parallel_first_views_leave_one_row_and_no_error(session, two_profiles):
    viewer, viewed = two_profiles

    async def view_once() -> None:
        async with db_helper.session_factory() as own_session:
            await SqlAlchemyProfileViewRepository(own_session).add(viewer, viewed, NOW)
            await own_session.commit()

    await asyncio.gather(*(view_once() for _ in range(15)))

    assert await view_rows(session) == [(viewer, viewed, NOW)]


@pytest.mark.parametrize("deleted", ["viewer", "viewed"])
async def test_deleting_either_profile_removes_the_view(session, two_profiles, deleted):
    viewer, viewed = two_profiles
    await SqlAlchemyProfileViewRepository(session).add(viewer, viewed, NOW)
    await session.commit()

    await session.execute(delete(ProfileModel).where(ProfileModel.id == (viewer if deleted == "viewer" else viewed)))
    await session.commit()

    count = (await session.execute(select(func.count()).select_from(ProfileViewModel))).scalar_one()
    assert count == 0
