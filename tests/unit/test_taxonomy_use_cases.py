import pytest

from app.application.use_cases.taxonomy import (
    CreateCustomTagUseCase,
    GetCategoriesUseCase,
    GetRoleFieldsByRoleUseCase,
    GetRolesByCategoryUseCase,
    SuggestTagsUseCase,
)
from app.core.config import TaxonomyConfig
from app.domain.entities import CategoryEntity, NewTagScopeEntity, RoleEntity, RoleFieldEntity, TagEntity
from app.domain.enums import AnalyticsEvent, RoleFieldType, TagStatus
from app.domain.exceptions import (
    CategoryNotFoundError,
    RoleNotFoundError,
    TagRejectedError,
    TagTitleInvalidError,
)
from app.domain.services import TagTitleValidator, normalize_tag_title
from tests.fixtures.fake_analytics import FakeAnalyticsTracker
from tests.fixtures.fake_taxonomy import FakeTaxonomyCacheRepository, FakeTaxonomyRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork

TECH = CategoryEntity(id=1, slug="tech_product", title="Tech & Product")
EDU = CategoryEntity(id=2, slug="edu_growth", title="Edu & Growth")
ENGINEERING = RoleEntity(id=10, category_id=1, slug="engineering", title="Engineering")
STUDY_MATE = RoleEntity(id=20, category_id=2, slug="study_mate", title="Study mate")
GRADE = RoleFieldEntity(
    id=100,
    role_id=10,
    key="grade",
    label="Grade",
    field_type=RoleFieldType.SELECT,
    options=[{"value": "junior", "label": "Junior"}],
    is_required=True,
    is_filterable=True,
)

USER_A = 1
USER_B = 2


def _tag(id: int, title: str, status: TagStatus = TagStatus.APPROVED, created_by: int | None = None) -> TagEntity:
    return TagEntity(
        id=id,
        title=title,
        normalized_title=normalize_tag_title(title),
        status=status,
        created_by_user_id=created_by,
    )


@pytest.fixture
def repo() -> FakeTaxonomyRepository:
    return FakeTaxonomyRepository(
        categories=[TECH, EDU], roles=[ENGINEERING, STUDY_MATE], role_fields=[GRADE]
    )


@pytest.fixture
def cache() -> FakeTaxonomyCacheRepository:
    return FakeTaxonomyCacheRepository()


@pytest.fixture
def validator() -> TagTitleValidator:
    config = TaxonomyConfig()
    return TagTitleValidator(
        min_length=config.tag_title_min_length,
        max_length=config.tag_title_max_length,
        pattern=config.tag_title_allowed_pattern,
    )


class TestGetCategories:
    async def test_cache_miss_reads_repository_and_fills_cache(self, repo, cache):
        result = await GetCategoriesUseCase(repo, cache).execute()

        assert result == [TECH, EDU]
        assert "get_categories" in repo.calls
        assert await cache.get_categories() == [TECH, EDU]

    async def test_cache_hit_does_not_touch_repository(self, repo, cache):
        await cache.set_categories([TECH])

        result = await GetCategoriesUseCase(repo, cache).execute()

        assert result == [TECH]
        assert repo.calls == []

    async def test_cache_outage_falls_back_to_repository(self, repo):
        outage_cache = FakeTaxonomyCacheRepository(outage=True)

        result = await GetCategoriesUseCase(repo, outage_cache).execute()

        assert result == [TECH, EDU]


class TestGetRolesByCategory:
    async def test_unknown_category_on_a_miss_raises_and_caches_nothing(self, repo, cache):
        with pytest.raises(CategoryNotFoundError):
            await GetRolesByCategoryUseCase(repo, cache).execute(999)

        assert "set_roles_by_category" not in cache.calls
        assert await cache.get_roles_by_category(999) is None

    async def test_unknown_category_is_not_remembered_between_calls(self, repo, cache):
        for _ in range(2):
            with pytest.raises(CategoryNotFoundError):
                await GetRolesByCategoryUseCase(repo, cache).execute(999)

        assert repo.calls.count("get_category_by_id") == 2

    async def test_cache_miss_fills_cache(self, repo, cache):
        result = await GetRolesByCategoryUseCase(repo, cache).execute(TECH.id)

        assert result == [ENGINEERING]
        assert await cache.get_roles_by_category(TECH.id) == [ENGINEERING]

    async def test_cache_hit_does_not_touch_the_repository_at_all(self, repo, cache):
        await cache.set_roles_by_category(TECH.id, [ENGINEERING])

        result = await GetRolesByCategoryUseCase(repo, cache).execute(TECH.id)

        assert result == [ENGINEERING]
        assert repo.calls == []

    async def test_cache_outage_falls_back_to_repository(self, repo):
        result = await GetRolesByCategoryUseCase(repo, FakeTaxonomyCacheRepository(outage=True)).execute(TECH.id)

        assert result == [ENGINEERING]


class TestGetRoleFieldsByRole:
    async def test_unknown_role_on_a_miss_raises_and_caches_nothing(self, repo, cache):
        with pytest.raises(RoleNotFoundError):
            await GetRoleFieldsByRoleUseCase(repo, cache).execute(999)

        assert "set_role_fields_by_role" not in cache.calls
        assert await cache.get_role_fields_by_role(999) is None

    async def test_cache_miss_fills_cache(self, repo, cache):
        result = await GetRoleFieldsByRoleUseCase(repo, cache).execute(ENGINEERING.id)

        assert result == [GRADE]
        assert await cache.get_role_fields_by_role(ENGINEERING.id) == [GRADE]

    async def test_cache_hit_does_not_touch_the_repository_at_all(self, repo, cache):
        await cache.set_role_fields_by_role(ENGINEERING.id, [GRADE])

        result = await GetRoleFieldsByRoleUseCase(repo, cache).execute(ENGINEERING.id)

        assert result == [GRADE]
        assert repo.calls == []

    async def test_cache_outage_falls_back_to_repository(self, repo):
        result = await GetRoleFieldsByRoleUseCase(repo, FakeTaxonomyCacheRepository(outage=True)).execute(
            ENGINEERING.id
        )

        assert result == [GRADE]


class TestSuggestTags:
    async def test_unknown_category_raises(self, repo):
        with pytest.raises(CategoryNotFoundError):
            await SuggestTagsUseCase(repo).execute("py", 999, None, USER_A)

    async def test_unknown_role_raises(self, repo):
        with pytest.raises(RoleNotFoundError):
            await SuggestTagsUseCase(repo).execute("py", TECH.id, 999, USER_A)

    async def test_role_from_another_category_raises(self, repo):
        with pytest.raises(RoleNotFoundError):
            await SuggestTagsUseCase(repo).execute("py", TECH.id, STUDY_MATE.id, USER_A)

    async def test_valid_scope_delegates_to_repository(self, repo):
        repo.tags[1] = _tag(1, "Python")

        result = await SuggestTagsUseCase(repo).execute("py", TECH.id, ENGINEERING.id, USER_A)

        assert [t.title for t in result] == ["Python"]

    async def test_repository_receives_the_query_scope_and_caller(self, repo):
        await SuggestTagsUseCase(repo).execute("py", TECH.id, ENGINEERING.id, USER_B)

        assert repo.suggest_calls == [
            {"query": "py", "category_id": TECH.id, "role_id": ENGINEERING.id, "user_id": USER_B}
        ]

    async def test_category_wide_search_passes_no_role(self, repo):
        await SuggestTagsUseCase(repo).execute("py", TECH.id, None, USER_A)

        assert repo.suggest_calls[0]["role_id"] is None


class TestCreateCustomTag:
    @pytest.fixture
    def uow(self) -> FakeUnitOfWork:
        return FakeUnitOfWork()

    @pytest.fixture
    def analytics(self) -> FakeAnalyticsTracker:
        return FakeAnalyticsTracker()

    @pytest.fixture
    def use_case(self, repo, validator, uow, analytics) -> CreateCustomTagUseCase:
        return CreateCustomTagUseCase(repo, validator, uow, analytics)

    async def test_new_tag_commits_exactly_once(self, use_case, uow):
        await use_case.execute("Node.js", TECH.id, ENGINEERING.id, USER_A)

        assert uow.commits == 1

    async def test_a_new_tag_is_reported_with_its_id_and_scope_but_not_its_title(self, use_case, analytics):
        tag = await use_case.execute("Node.js", TECH.id, ENGINEERING.id, USER_A)

        [event] = analytics.events
        assert (event.event, event.user_id) == (AnalyticsEvent.TAG_CREATED, USER_A)
        assert event.properties == {"tag_id": tag.id, "category_id": TECH.id, "role_id": ENGINEERING.id}

    async def test_a_failed_commit_is_not_reported(self, use_case, uow, analytics):
        uow.fail_on_commit = True

        with pytest.raises(RuntimeError):
            await use_case.execute("Node.js", TECH.id, ENGINEERING.id, USER_A)

        assert analytics.events == []

    async def test_a_category_wide_tag_is_reported_without_a_role(self, use_case, analytics):
        await use_case.execute("Node.js", TECH.id, None, USER_A)

        assert analytics.events[0].properties["role_id"] is None

    async def test_reusing_an_existing_tag_is_not_a_new_tag(self, use_case, repo, analytics):
        repo.tags[1] = _tag(1, "Python")

        await use_case.execute("python", TECH.id, ENGINEERING.id, USER_A)

        assert analytics.events == []

    async def test_losing_the_insert_race_is_not_a_new_tag(self, use_case, repo, analytics):
        repo.lose_race_once = True

        await use_case.execute("Node.js", TECH.id, ENGINEERING.id, USER_A)

        assert analytics.events == []

    async def test_a_rejected_tag_is_not_reported(self, use_case, repo, analytics):
        repo.tags[1] = _tag(1, "Spam", TagStatus.REJECTED)

        with pytest.raises(TagRejectedError):
            await use_case.execute("spam", TECH.id, ENGINEERING.id, USER_A)

        assert analytics.events == []

    async def test_existing_tag_still_commits_once_for_the_new_scope(self, use_case, repo, uow):
        repo.tags[1] = _tag(1, "Python")

        await use_case.execute("python", TECH.id, ENGINEERING.id, USER_A)

        assert uow.commits == 1

    async def test_lost_race_commits_exactly_once(self, use_case, repo, uow):
        repo.lose_race_once = True

        await use_case.execute("Node.js", TECH.id, ENGINEERING.id, USER_A)

        assert uow.commits == 1

    async def test_rejected_tag_does_not_commit(self, use_case, repo, uow):
        repo.tags[1] = _tag(1, "Spam", TagStatus.REJECTED)

        with pytest.raises(TagRejectedError):
            await use_case.execute("spam", TECH.id, ENGINEERING.id, USER_A)

        assert uow.commits == 0

    async def test_invalid_title_does_not_commit(self, use_case, uow):
        with pytest.raises(TagTitleInvalidError):
            await use_case.execute("<script>", TECH.id, ENGINEERING.id, USER_A)

        assert uow.commits == 0

    async def test_unknown_category_does_not_commit(self, use_case, uow):
        with pytest.raises(CategoryNotFoundError):
            await use_case.execute("Python", 999, None, USER_A)

        assert uow.commits == 0

    async def test_unknown_role_does_not_commit(self, use_case, uow):
        with pytest.raises(RoleNotFoundError):
            await use_case.execute("Python", TECH.id, 999, USER_A)

        assert uow.commits == 0

    async def test_new_title_creates_pending_tag_with_scope(self, use_case, repo):
        tag = await use_case.execute("Node.js", TECH.id, ENGINEERING.id, USER_A)

        assert tag.status == TagStatus.PENDING
        assert tag.title == "Node.js"
        assert tag.normalized_title == "node.js"
        assert tag.created_by_user_id == USER_A
        assert repo.scopes == [NewTagScopeEntity(tag_id=tag.id, category_id=TECH.id, role_id=ENGINEERING.id)]

    async def test_stored_title_is_cleaned_but_keeps_case(self, use_case):
        tag = await use_case.execute("  Machine   Learning ", TECH.id, None, USER_A)

        assert tag.title == "Machine Learning"

    async def test_duplicate_in_other_case_returns_existing_tag(self, use_case, repo):
        first = await use_case.execute("Node.js", TECH.id, ENGINEERING.id, USER_A)

        second = await use_case.execute("  NODE.JS ", TECH.id, ENGINEERING.id, USER_B)

        assert second.id == first.id
        assert second.title == "Node.js"  # canonical title, not what the caller typed
        assert len(repo.tags) == 1

    async def test_pending_tag_of_another_user_is_reused(self, use_case, repo):
        repo.tags[1] = _tag(1, "Rust", TagStatus.PENDING, created_by=USER_A)

        tag = await use_case.execute("rust", TECH.id, ENGINEERING.id, USER_B)

        assert tag.id == 1
        assert tag.created_by_user_id == USER_A
        assert len(repo.tags) == 1

    async def test_approved_tag_is_reused_without_create(self, use_case, repo):
        repo.tags[1] = _tag(1, "Python")

        tag = await use_case.execute("PYTHON", TECH.id, ENGINEERING.id, USER_A)

        assert tag.id == 1
        assert "create_tag" not in repo.calls

    async def test_rejected_tag_raises_and_creates_no_scope(self, use_case, repo):
        repo.tags[1] = _tag(1, "Spam", TagStatus.REJECTED)

        with pytest.raises(TagRejectedError):
            await use_case.execute("spam", TECH.id, ENGINEERING.id, USER_A)

        assert repo.scopes == []

    async def test_lost_race_rereads_and_returns_the_winners_tag(self, use_case, repo):
        repo.lose_race_once = True

        tag = await use_case.execute("Node.js", TECH.id, ENGINEERING.id, USER_A)

        assert len(repo.tags) == 1
        assert tag.id == next(iter(repo.tags))
        assert tag.created_by_user_id is None  # inserted by the other request, not by us
        assert repo.calls.count("get_tag_by_normalized_title") == 2
        assert len(repo.scopes) == 1

    async def test_lost_race_with_category_wide_scope(self, use_case, repo):
        repo.lose_race_once = True

        await use_case.execute("Go", TECH.id, None, USER_A)

        assert repo.scopes[0].role_id is None

    async def test_invalid_title_raises_before_any_repository_call(self, use_case, repo):
        with pytest.raises(TagTitleInvalidError):
            await use_case.execute("<script>", TECH.id, ENGINEERING.id, USER_A)

        assert repo.calls == []

    async def test_whitespace_only_title_is_invalid(self, use_case, repo):
        with pytest.raises(TagTitleInvalidError):
            await use_case.execute("   ", TECH.id, None, USER_A)

        assert repo.calls == []

    async def test_unknown_category_raises(self, use_case, repo):
        with pytest.raises(CategoryNotFoundError):
            await use_case.execute("Python", 999, None, USER_A)

        assert repo.tags == {}

    async def test_unknown_role_raises(self, use_case, repo):
        with pytest.raises(RoleNotFoundError):
            await use_case.execute("Python", TECH.id, 999, USER_A)

        assert repo.tags == {}

    async def test_role_from_another_category_raises(self, use_case, repo):
        with pytest.raises(RoleNotFoundError):
            await use_case.execute("Python", TECH.id, STUDY_MATE.id, USER_A)

        assert repo.tags == {}

    async def test_existing_tag_gets_scope_for_another_category(self, use_case, repo):
        first = await use_case.execute("Python", TECH.id, ENGINEERING.id, USER_A)

        again = await use_case.execute("python", EDU.id, STUDY_MATE.id, USER_B)

        assert again.id == first.id
        assert len(repo.tags) == 1
        assert {(s.category_id, s.role_id) for s in repo.scopes} == {
            (TECH.id, ENGINEERING.id),
            (EDU.id, STUDY_MATE.id),
        }

    async def test_repeating_the_same_request_is_idempotent(self, use_case, repo):
        await use_case.execute("Python", TECH.id, ENGINEERING.id, USER_A)
        await use_case.execute("Python", TECH.id, ENGINEERING.id, USER_A)

        assert len(repo.tags) == 1
        assert len(repo.scopes) == 1

    async def test_c_family_titles_create_distinct_tags(self, use_case, repo):
        ids = {(await use_case.execute(t, TECH.id, None, USER_A)).id for t in ("C", "C#", "C++")}

        assert len(ids) == 3
