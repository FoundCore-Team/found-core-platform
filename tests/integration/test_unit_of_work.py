"""Transaction boundaries: repositories never commit, use cases do (via IUnitOfWork).

Each check reads through a *fresh* session on purpose: a session always sees its
own uncommitted changes, so only a second session tells committed data from not.
"""

import pytest

from app.application.use_cases import CreateCustomTagUseCase
from app.domain.entities import NewTagEntity
from app.domain.enums import TagStatus
from app.infrastructure.helpers import db_helper
from app.infrastructure.repositories import SqlAlchemyTaxonomyRepository, SqlAlchemyUserRepository
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.factories import make_new_user_entity
from tests.fixtures.taxonomy_data import count_tag_scopes, count_tags


async def _fresh_counts() -> tuple[int, int]:
    async with db_helper.session_factory() as fresh:
        return await count_tags(fresh), await count_tag_scopes(fresh)


async def test_taxonomy_repository_does_not_commit(session):
    repo = SqlAlchemyTaxonomyRepository(session, suggest_limit=20)

    await repo.create_tag(
        NewTagEntity(title="Node.js", normalized_title="node.js", status=TagStatus.PENDING, created_by_user_id=None)
    )

    assert await count_tags(session) == 1  # visible to its own session
    assert await _fresh_counts() == (0, 0)  # but not committed


async def test_user_repository_does_not_commit(session):
    await SqlAlchemyUserRepository(session).create(make_new_user_entity(telegram_id=930001))

    async with db_helper.session_factory() as fresh:
        assert await SqlAlchemyUserRepository(fresh).get_by_telegram_id(930001) is None


async def test_create_custom_tag_use_case_commits_tag_and_scope(session, container, taxonomy_data):
    t = taxonomy_data
    user, _ = await create_user_with_headers(session, telegram_id=930002)

    await container.create_custom_tag_use_case().execute(
        title="Node.js", category_id=t.tech_category_id, role_id=t.engineering_role_id, user_id=user.id
    )

    assert await _fresh_counts() == (1, 1)


async def test_failure_after_tag_insert_leaves_no_orphan_tag(session, container, taxonomy_data):
    t = taxonomy_data
    user, _ = await create_user_with_headers(session, telegram_id=930003)
    repo = container.taxonomy_repo()

    async def broken_create_tag_scope(scope):
        raise RuntimeError("database went away")

    repo.create_tag_scope = broken_create_tag_scope
    use_case = CreateCustomTagUseCase(
        taxonomy_repository=repo,
        tag_title_validator=container.tag_title_validator(),
        unit_of_work=container.unit_of_work(),
        analytics_tracker=container.analytics_tracker,
    )

    with pytest.raises(RuntimeError):
        await use_case.execute(
            title="Node.js", category_id=t.tech_category_id, role_id=t.engineering_role_id, user_id=user.id
        )
    # In production the request-scoped session is closed after the failure, which rolls back.
    await session.close()

    assert await _fresh_counts() == (0, 0)
