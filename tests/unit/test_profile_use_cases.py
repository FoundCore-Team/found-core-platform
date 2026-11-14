from dataclasses import replace

import pytest

from app.application.services.profile_content_validator import ProfileContentValidator
from app.application.services.timezone_catalog import TimezoneCatalog
from app.application.use_cases import (
    ActivateProfileUseCase,
    CreateProfileUseCase,
    DeleteProfileUseCase,
    GetMyProfilesUseCase,
    GetProfileFormConfigUseCase,
    GetProfileUseCase,
    PauseProfileUseCase,
    ResumeProfileUseCase,
    UpdateProfileUseCase,
)
from app.core.config import ProfileConfig
from app.domain.entities import (
    CategoryEntity,
    NewTagScopeEntity,
    ProfileFormEntity,
    RoleEntity,
    RoleFieldEntity,
    TagEntity,
)
from app.domain.enums import AnalyticsEvent, EmbeddingStatus, ProfileStatus, RoleFieldType, TagStatus
from app.domain.exceptions import (
    CategoryNotFoundError,
    InvalidExtraAttributesError,
    ProfileAlreadyExistsError,
    ProfileHiddenError,
    ProfileNotActivatableError,
    ProfileNotFoundError,
    ProfileTagInvalidError,
    RoleNotFoundError,
    TagRejectedError,
)
from app.domain.services import ExtraAttributesValidator, ProfileFormValidator
from tests.fixtures.factories import make_user_entity
from tests.fixtures.fake_analytics import FakeAnalyticsTracker
from tests.fixtures.fake_embedding_queue import FakeEmbeddingQueue
from tests.fixtures.fake_profile_embedding_repository import FakeProfileEmbeddingRepository
from tests.fixtures.fake_profile_repository import FakeProfileRepository
from tests.fixtures.fake_taxonomy import FakeTaxonomyRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.fixtures.fake_user_repository import FakeUserRepository

TECH = CategoryEntity(id=1, slug="tech_product", title="Tech & Product")
EDU = CategoryEntity(id=2, slug="edu_growth", title="Edu & Growth")
ENGINEERING = RoleEntity(id=10, category_id=1, slug="engineering", title="Engineering")
DESIGN = RoleEntity(id=11, category_id=1, slug="design", title="Design")
STUDY_MATE = RoleEntity(id=20, category_id=2, slug="study_mate", title="Study mate")
GRADE = RoleFieldEntity(
    id=100,
    role_id=10,
    key="grade",
    label="Grade",
    field_type=RoleFieldType.SELECT,
    options=[{"value": "junior", "label": "Junior"}, {"value": "senior", "label": "Senior"}],
    is_required=True,
    is_filterable=True,
)
WORKLOAD = RoleFieldEntity(
    id=101,
    role_id=10,
    key="workload",
    label="Workload",
    field_type=RoleFieldType.SELECT,
    options=[{"value": "full_time", "label": "Full time"}],
    is_required=False,
    is_filterable=True,
)

OTHER_USER = 99


def _tag(tag_id: int, status: TagStatus = TagStatus.APPROVED, created_by: int | None = None) -> TagEntity:
    return TagEntity(
        id=tag_id,
        title=f"tag-{tag_id}",
        normalized_title=f"tag-{tag_id}",
        status=status,
        created_by_user_id=created_by,
    )


# 1-6 approved, 7 rejected, 8 pending (created by someone else): all in scope of engineering and design.
# 9 is approved but scoped only to the study_mate role of the other category.
TAGS = [
    *[_tag(i) for i in range(1, 7)],
    _tag(7, TagStatus.REJECTED),
    _tag(8, TagStatus.PENDING, created_by=OTHER_USER),
    _tag(9),
]
VALID_TAG_IDS = [1, 2, 3, 4, 5]
TEXT = "x" * 200


def _form(**overrides) -> ProfileFormEntity:
    values = dict(
        country_code="DE",
        timezone="Europe/Berlin",
        bio=TEXT,
        goals_description=TEXT,
        tag_ids=VALID_TAG_IDS,
    )
    values.update(overrides)
    return ProfileFormEntity(**values)


class Env:
    """Fakes plus the helpers that build profiles through the real use cases."""

    def __init__(self) -> None:
        self.taxonomy = FakeTaxonomyRepository(
            categories=[TECH, EDU],
            roles=[ENGINEERING, DESIGN, STUDY_MATE],
            role_fields=[GRADE, WORKLOAD],
            tags=[replace(tag) for tag in TAGS],
        )
        self.taxonomy.scopes = [
            NewTagScopeEntity(tag_id=tag.id, category_id=TECH.id, role_id=role.id)
            for tag in TAGS[:8]
            for role in (ENGINEERING, DESIGN)
        ] + [NewTagScopeEntity(tag_id=9, category_id=EDU.id, role_id=STUDY_MATE.id)]
        self.profiles = FakeProfileRepository(known_tags=TAGS)
        self.users = FakeUserRepository([make_user_entity(id=1, telegram_id=1), make_user_entity(id=2, telegram_id=2)])
        self.embeddings = FakeProfileEmbeddingRepository()
        self.queue = FakeEmbeddingQueue()
        self.uow = FakeUnitOfWork()
        self.analytics = FakeAnalyticsTracker()
        config = ProfileConfig()
        self.content_validator = ProfileContentValidator(
            self.taxonomy,
            ProfileFormValidator(
                bio_min_length=config.bio_min_length,
                bio_max_length=config.bio_max_length,
                goals_min_length=config.goals_min_length,
                goals_max_length=config.goals_max_length,
                tags_min_count=config.tags_min_count,
                tags_max_count=config.tags_max_count,
                # A second zone and a second country, so a change of either can be saved (the update event tests).
                timezones_by_country={"DE": ["Europe/Berlin", "Europe/Busingen"], "AT": ["Europe/Vienna"]},
            ),
            ExtraAttributesValidator(),
        )

    async def user(self, user_id: int = 1):
        """The user as get_current_user would load it for the next request."""
        return await self.users.get_by_id(user_id)

    async def active_profile_id(self, user_id: int = 1) -> int | None:
        return (await self.user(user_id)).active_profile_id

    async def create(self, user_id: int = 1, role: RoleEntity = ENGINEERING, form: ProfileFormEntity | None = None):
        extra_attributes = {"grade": "junior"} if role.id == ENGINEERING.id else {}
        profile = await self.create_use_case().execute(
            await self.user(user_id), role.category_id, role.id, form or _form(), extra_attributes
        )
        self.uow.commits = 0
        self.queue.enqueued.clear()
        self.analytics.events.clear()
        return profile

    def create_use_case(self) -> CreateProfileUseCase:
        return CreateProfileUseCase(
            self.profiles,
            self.users,
            self.taxonomy,
            self.content_validator,
            self.embeddings,
            self.queue,
            self.uow,
            self.analytics,
        )

    def update_use_case(self) -> UpdateProfileUseCase:
        return UpdateProfileUseCase(
            self.profiles, self.content_validator, self.embeddings, self.queue, self.uow, self.analytics
        )

    def activate_use_case(self) -> ActivateProfileUseCase:
        return ActivateProfileUseCase(self.profiles, self.users, self.uow, self.analytics)

    def pause_use_case(self) -> PauseProfileUseCase:
        return PauseProfileUseCase(self.profiles, self.users, self.uow, self.analytics)

    def resume_use_case(self) -> ResumeProfileUseCase:
        return ResumeProfileUseCase(self.profiles, self.users, self.uow, self.analytics)

    def delete_use_case(self) -> DeleteProfileUseCase:
        return DeleteProfileUseCase(self.profiles, self.users, self.uow, self.analytics)


@pytest.fixture
def env() -> Env:
    return Env()


def _create_use_case(env: Env) -> CreateProfileUseCase:
    return env.create_use_case()


class TestCreateProfile:
    async def test_first_profile_is_active_and_becomes_the_users_active_profile(self, env):
        profile = await _create_use_case(env).execute(
            await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"}
        )

        assert profile.status is ProfileStatus.ACTIVE
        assert [t.id for t in profile.tags] == VALID_TAG_IDS
        assert await env.active_profile_id() == profile.id
        assert env.uow.commits == 1

    async def test_creating_a_profile_increments_usage_of_its_tags(self, env):
        await env.create()

        assert [env.taxonomy.tags[i].usage_count for i in VALID_TAG_IDS] == [1] * 5
        assert env.taxonomy.tags[6].usage_count == 0

    async def test_usage_is_not_incremented_for_a_duplicate_profile(self, env):
        await env.create()

        with pytest.raises(ProfileAlreadyExistsError):
            await _create_use_case(env).execute(
                await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"}
            )

        assert [env.taxonomy.tags[i].usage_count for i in VALID_TAG_IDS] == [1] * 5

    async def test_second_profile_does_not_change_the_active_profile(self, env):
        first = await env.create(role=ENGINEERING)

        second = await _create_use_case(env).execute(await env.user(), TECH.id, DESIGN.id, _form(), {})

        assert second.id != first.id
        assert await env.active_profile_id() == first.id

    async def test_text_is_cleaned_before_saving(self, env):
        profile = await _create_use_case(env).execute(
            await env.user(),
            TECH.id,
            ENGINEERING.id,
            _form(country_code=" de ", bio=f"  {TEXT}  "),
            {"grade": "junior", "workload": "  "},
        )

        assert profile.country_code == "DE"
        assert profile.bio == TEXT
        assert profile.extra_attributes == {"grade": "junior"}

    async def test_repeat_for_the_same_role_raises_with_the_existing_profile(self, env):
        existing = await env.create()

        with pytest.raises(ProfileAlreadyExistsError) as raised:
            await _create_use_case(env).execute(await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"})

        assert raised.value.profile.id == existing.id
        assert env.uow.commits == 0
        assert len(env.profiles.profiles) == 1

    async def test_losing_the_insert_race_raises_already_exists(self, env):
        env.profiles.lose_race_once = True

        with pytest.raises(ProfileAlreadyExistsError) as raised:
            await _create_use_case(env).execute(await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"})

        assert raised.value.profile.user_id == 1
        assert env.uow.commits == 0

    async def test_unknown_category_raises(self, env):
        with pytest.raises(CategoryNotFoundError):
            await _create_use_case(env).execute(await env.user(), 999, ENGINEERING.id, _form(), {})

    @pytest.mark.parametrize("role_id", [999, STUDY_MATE.id])
    async def test_unknown_role_or_role_of_another_category_raises(self, env, role_id):
        with pytest.raises(RoleNotFoundError):
            await _create_use_case(env).execute(await env.user(), TECH.id, role_id, _form(), {})

        assert env.uow.commits == 0

    async def test_tag_outside_the_scope_is_rejected(self, env):
        with pytest.raises(ProfileTagInvalidError):
            await _create_use_case(env).execute(
                await env.user(), TECH.id, ENGINEERING.id, _form(tag_ids=[1, 2, 3, 4, 9]), {"grade": "junior"}
            )

        assert env.profiles.profiles == []

    async def test_unknown_tag_id_is_rejected(self, env):
        with pytest.raises(ProfileTagInvalidError):
            await _create_use_case(env).execute(
                await env.user(), TECH.id, ENGINEERING.id, _form(tag_ids=[1, 2, 3, 4, 12345]), {"grade": "junior"}
            )

    async def test_rejected_tag_is_refused(self, env):
        with pytest.raises(TagRejectedError):
            await _create_use_case(env).execute(
                await env.user(), TECH.id, ENGINEERING.id, _form(tag_ids=[1, 2, 3, 4, 7]), {"grade": "junior"}
            )

        assert env.uow.commits == 0

    async def test_pending_tag_of_another_user_is_allowed(self, env):
        profile = await _create_use_case(env).execute(
            await env.user(), TECH.id, ENGINEERING.id, _form(tag_ids=[1, 2, 3, 4, 8]), {"grade": "junior"}
        )

        assert 8 in [t.id for t in profile.tags]

    async def test_missing_required_attribute_is_refused(self, env):
        with pytest.raises(InvalidExtraAttributesError):
            await _create_use_case(env).execute(await env.user(), TECH.id, ENGINEERING.id, _form(), {})

        assert env.profiles.profiles == []
        assert env.uow.commits == 0

    async def test_unknown_attribute_value_is_refused(self, env):
        with pytest.raises(InvalidExtraAttributesError):
            await _create_use_case(env).execute(
                await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "wizard"}
            )

    async def test_marks_the_embedding_pending_and_enqueues_the_job(self, env):
        profile = await _create_use_case(env).execute(
            await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"}
        )

        [row] = env.embeddings.rows
        assert (row.profile_id, row.status) == (profile.id, EmbeddingStatus.PENDING)
        assert row.source_updated_at == profile.updated_at
        assert env.queue.enqueued == [profile.id]
        assert env.uow.commits == 1

    async def test_enqueues_only_after_the_commit(self, env):
        commits_seen_by_queue: list[int] = []

        async def record(profile_id: int) -> None:
            commits_seen_by_queue.append(env.uow.commits)

        env.queue.enqueue = record

        await _create_use_case(env).execute(await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"})

        assert commits_seen_by_queue == [1]

    @pytest.mark.parametrize("lose_race", [False, True])
    async def test_nothing_is_marked_or_enqueued_for_a_duplicate(self, env, lose_race):
        if not lose_race:
            await env.create()
        else:
            env.profiles.lose_race_once = True

        with pytest.raises(ProfileAlreadyExistsError):
            await _create_use_case(env).execute(await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"})

        # Only the row of the profile that already existed.
        assert len(env.embeddings.rows) == (0 if lose_race else 1)
        assert env.queue.enqueued == []

    async def test_nothing_is_marked_or_enqueued_for_invalid_content(self, env):
        with pytest.raises(InvalidExtraAttributesError):
            await _create_use_case(env).execute(await env.user(), TECH.id, ENGINEERING.id, _form(), {})

        assert env.embeddings.rows == []
        assert env.queue.enqueued == []


class TestUpdateProfile:
    async def test_replaces_content_and_tags_and_commits_once(self, env):
        profile = await env.create()

        updated = await env.update_use_case().execute(
            1, profile.id, _form(bio="y" * 300, tag_ids=[2, 3, 4, 5, 6]), {"grade": "senior"}
        )

        assert updated.bio == "y" * 300
        assert [t.id for t in updated.tags] == [2, 3, 4, 5, 6]
        assert updated.extra_attributes == {"grade": "senior"}
        assert updated.updated_at > profile.updated_at
        assert env.uow.commits == 1

    async def test_omitted_optional_attribute_is_removed(self, env):
        profile = await env.create()
        await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior", "workload": "full_time"})

        updated = await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior"})

        assert updated.extra_attributes == {"grade": "junior"}

    async def test_erasing_a_required_attribute_is_refused(self, env):
        profile = await env.create()

        with pytest.raises(InvalidExtraAttributesError):
            await env.update_use_case().execute(1, profile.id, _form(), {})

        assert env.uow.commits == 0

    async def test_does_not_change_status_category_or_role(self, env):
        profile = await env.create()
        await env.pause_use_case().execute(await env.user(), profile.id)

        updated = await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior"})

        assert updated.status is ProfileStatus.PAUSED
        assert (updated.category_id, updated.role_id) == (TECH.id, ENGINEERING.id)

    async def test_someone_elses_profile_looks_missing(self, env):
        profile = await env.create(user_id=1)

        with pytest.raises(ProfileNotFoundError):
            await env.update_use_case().execute(2, profile.id, _form(), {"grade": "junior"})

    async def test_unknown_profile_raises(self, env):
        with pytest.raises(ProfileNotFoundError):
            await env.update_use_case().execute(1, 999, _form(), {})

    async def test_hidden_profile_cannot_be_edited(self, env):
        profile = await env.create()
        await env.profiles.set_status(profile.id, ProfileStatus.HIDDEN_BY_ADMIN)

        with pytest.raises(ProfileHiddenError):
            await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior"})

        assert env.uow.commits == 0

    async def test_marks_the_embedding_pending_with_the_new_updated_at_and_enqueues(self, env):
        profile = await env.create()
        await env.embeddings.save(profile.id, [0.0], "hash", "model", profile.updated_at)

        updated = await env.update_use_case().execute(1, profile.id, _form(bio="y" * 300), {"grade": "junior"})

        [row] = env.embeddings.rows
        assert row.status is EmbeddingStatus.PENDING
        assert row.source_updated_at == updated.updated_at
        assert env.queue.enqueued == [profile.id]
        assert env.uow.commits == 1

    async def test_enqueues_only_after_the_commit(self, env):
        profile = await env.create()
        commits_seen_by_queue: list[int] = []

        async def record(profile_id: int) -> None:
            commits_seen_by_queue.append(env.uow.commits)

        env.queue.enqueue = record

        await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior"})

        assert commits_seen_by_queue == [1]

    async def test_nothing_is_enqueued_when_the_update_is_refused(self, env):
        profile = await env.create()
        await env.profiles.set_status(profile.id, ProfileStatus.HIDDEN_BY_ADMIN)
        before = env.embeddings.rows

        with pytest.raises(ProfileHiddenError):
            await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior"})
        with pytest.raises(ProfileNotFoundError):
            await env.update_use_case().execute(2, profile.id, _form(), {"grade": "junior"})

        assert env.embeddings.rows == before
        assert env.queue.enqueued == []


class TestReadProfiles:
    async def test_get_my_profiles_returns_only_own_profiles(self, env):
        mine = await env.create(user_id=1)
        await env.create(user_id=2)

        profiles = await GetMyProfilesUseCase(env.profiles).execute(1)

        assert [p.id for p in profiles] == [mine.id]

    async def test_get_profile_of_another_user_raises_not_found(self, env):
        profile = await env.create(user_id=1)

        with pytest.raises(ProfileNotFoundError):
            await GetProfileUseCase(env.profiles).execute(2, profile.id)

    async def test_get_profile_returns_own_profile(self, env):
        profile = await env.create(user_id=1)

        found = await GetProfileUseCase(env.profiles).execute(1, profile.id)

        assert found.id == profile.id


class TestActivateProfile:
    async def test_makes_another_active_profile_the_current_one(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)
        assert await env.active_profile_id() == first.id

        await env.activate_use_case().execute(await env.user(), second.id)

        assert await env.active_profile_id() == second.id
        assert env.uow.commits == 1

    async def test_activating_the_current_profile_is_a_no_op(self, env):
        profile = await env.create()

        returned = await env.activate_use_case().execute(await env.user(), profile.id)

        assert returned.id == profile.id
        assert env.uow.commits == 0

    async def test_paused_profile_cannot_be_activated(self, env):
        await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)
        await env.pause_use_case().execute(await env.user(), second.id)

        with pytest.raises(ProfileNotActivatableError):
            await env.activate_use_case().execute(await env.user(), second.id)

    async def test_hidden_profile_cannot_be_activated(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)
        await env.profiles.set_status(second.id, ProfileStatus.HIDDEN_BY_ADMIN)

        with pytest.raises(ProfileHiddenError):
            await env.activate_use_case().execute(await env.user(), second.id)

        assert await env.active_profile_id() == first.id

    async def test_someone_elses_profile_looks_missing(self, env):
        profile = await env.create(user_id=1)

        with pytest.raises(ProfileNotFoundError):
            await env.activate_use_case().execute(await env.user(2), profile.id)


class TestPauseProfile:
    async def test_pausing_the_only_profile_clears_the_active_profile(self, env):
        profile = await env.create()

        paused = await env.pause_use_case().execute(await env.user(), profile.id)

        assert paused.status is ProfileStatus.PAUSED
        assert (await env.profiles.get_by_id(profile.id)).status is ProfileStatus.PAUSED
        assert await env.active_profile_id() is None
        assert env.uow.commits == 1

    async def test_pausing_the_active_profile_moves_the_pointer_to_the_newest_remaining_active(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)

        await env.pause_use_case().execute(await env.user(), first.id)

        assert await env.active_profile_id() == second.id

    async def test_pausing_a_non_active_profile_keeps_the_pointer(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)

        await env.pause_use_case().execute(await env.user(), second.id)

        assert await env.active_profile_id() == first.id

    async def test_pointer_skips_paused_profiles(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)
        await env.pause_use_case().execute(await env.user(), second.id)

        await env.pause_use_case().execute(await env.user(), first.id)

        assert await env.active_profile_id() is None

    async def test_pausing_a_paused_profile_is_a_no_op(self, env):
        profile = await env.create()
        await env.pause_use_case().execute(await env.user(), profile.id)
        env.uow.commits = 0

        returned = await env.pause_use_case().execute(await env.user(), profile.id)

        assert returned.status is ProfileStatus.PAUSED
        assert env.uow.commits == 0

    async def test_hidden_profile_cannot_be_paused(self, env):
        profile = await env.create()
        await env.profiles.set_status(profile.id, ProfileStatus.HIDDEN_BY_ADMIN)

        with pytest.raises(ProfileHiddenError):
            await env.pause_use_case().execute(await env.user(), profile.id)

    async def test_someone_elses_profile_looks_missing(self, env):
        profile = await env.create(user_id=1)

        with pytest.raises(ProfileNotFoundError):
            await env.pause_use_case().execute(await env.user(2), profile.id)


class TestResumeProfile:
    async def test_resuming_when_there_is_no_active_profile_makes_it_active(self, env):
        profile = await env.create()
        await env.pause_use_case().execute(await env.user(), profile.id)
        env.uow.commits = 0

        resumed = await env.resume_use_case().execute(await env.user(), profile.id)

        assert resumed.status is ProfileStatus.ACTIVE
        assert await env.active_profile_id() == profile.id
        assert env.uow.commits == 1

    async def test_resuming_keeps_the_current_active_profile(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)
        await env.pause_use_case().execute(await env.user(), second.id)

        await env.resume_use_case().execute(await env.user(), second.id)

        assert await env.active_profile_id() == first.id

    async def test_resuming_an_active_profile_is_a_no_op(self, env):
        profile = await env.create()

        returned = await env.resume_use_case().execute(await env.user(), profile.id)

        assert returned.status is ProfileStatus.ACTIVE
        assert env.uow.commits == 0

    async def test_hidden_profile_cannot_be_resumed(self, env):
        profile = await env.create()
        await env.profiles.set_status(profile.id, ProfileStatus.HIDDEN_BY_ADMIN)

        with pytest.raises(ProfileHiddenError):
            await env.resume_use_case().execute(await env.user(), profile.id)

    async def test_someone_elses_profile_looks_missing(self, env):
        profile = await env.create(user_id=1)

        with pytest.raises(ProfileNotFoundError):
            await env.resume_use_case().execute(await env.user(2), profile.id)


class TestDeleteProfile:
    async def test_deleting_the_only_profile_clears_the_active_profile(self, env):
        profile = await env.create()

        await env.delete_use_case().execute(await env.user(), profile.id)

        assert await env.profiles.get_by_id(profile.id) is None
        assert await env.active_profile_id() is None
        assert env.uow.commits == 1

    async def test_deleting_a_profile_keeps_tag_usage(self, env):
        profile = await env.create()

        await env.delete_use_case().execute(await env.user(), profile.id)

        assert [env.taxonomy.tags[i].usage_count for i in VALID_TAG_IDS] == [1] * 5

    async def test_deleting_the_active_profile_moves_the_pointer_to_the_newest_remaining_active(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)

        await env.delete_use_case().execute(await env.user(), first.id)

        assert await env.active_profile_id() == second.id

    async def test_deleting_a_non_active_profile_keeps_the_pointer(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)

        await env.delete_use_case().execute(await env.user(), second.id)

        assert await env.active_profile_id() == first.id

    async def test_deleting_leaves_paused_profiles_out_of_the_pointer(self, env):
        first = await env.create(role=ENGINEERING)
        second = await env.create(role=DESIGN)
        await env.pause_use_case().execute(await env.user(), second.id)

        await env.delete_use_case().execute(await env.user(), first.id)

        assert await env.active_profile_id() is None

    async def test_someone_elses_profile_looks_missing_and_stays(self, env):
        profile = await env.create(user_id=1)

        with pytest.raises(ProfileNotFoundError):
            await env.delete_use_case().execute(await env.user(2), profile.id)

        assert await env.profiles.get_by_id(profile.id) is not None
        assert env.uow.commits == 0

    async def test_unknown_profile_raises(self, env):
        with pytest.raises(ProfileNotFoundError):
            await env.delete_use_case().execute(await env.user(), 999)


class TestProfileFormConfig:
    async def test_returns_the_configured_limits_and_countries_with_their_zones(self):
        config = ProfileConfig()

        returned_config, countries = await GetProfileFormConfigUseCase(config, TimezoneCatalog()).execute()

        assert returned_config is config
        germany = next(c for c in countries if c.code == "DE")
        assert germany.name == "Germany"
        assert {z.id for z in germany.timezones} >= {"Europe/Berlin"}
        berlin = next(z for z in germany.timezones if z.id == "Europe/Berlin")
        assert berlin.label == "Berlin"
        assert berlin.utc_offset in ("+01:00", "+02:00")

    async def test_allowed_country_codes_narrow_the_list(self):
        config = ProfileConfig(allowed_country_codes=["DE", "JP"])

        _, countries = await GetProfileFormConfigUseCase(config, TimezoneCatalog()).execute()

        assert sorted(c.code for c in countries) == ["DE", "JP"]

    async def test_every_country_has_at_least_one_timezone(self):
        _, countries = await GetProfileFormConfigUseCase(ProfileConfig(), TimezoneCatalog()).execute()

        assert countries
        assert all(c.timezones for c in countries)


NO_FIELD_CHANGED = {
    "country_changed": False,
    "timezone_changed": False,
    "bio_changed": False,
    "goals_changed": False,
    "tags_changed": False,
    "attributes_changed": False,
}


class TestProfileEvents:
    async def test_a_new_profile_is_reported_after_the_commit_without_its_texts(self, env):
        profile = await env.create_use_case().execute(
            await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"}
        )

        [event] = env.analytics.events
        assert (event.event, event.user_id) == (AnalyticsEvent.PROFILE_CREATED, 1)
        assert event.properties == {
            "actor_profile_id": profile.id,
            "category_id": TECH.id,
            "role_id": ENGINEERING.id,
            "tags_count": 5,
            "is_first_profile": True,
        }
        assert TEXT not in event.properties.values()
        assert env.uow.commits == 1

    async def test_a_second_profile_is_not_the_first(self, env):
        await env.create()

        await env.create_use_case().execute(await env.user(), TECH.id, DESIGN.id, _form(), {})

        assert env.analytics.events[0].properties["is_first_profile"] is False

    async def test_a_profile_created_while_the_others_are_paused_is_not_the_first_either(self, env):
        first = await env.create()
        await env.pause_use_case().execute(await env.user(), first.id)
        assert await env.active_profile_id() is None

        await env.create_use_case().execute(await env.user(), TECH.id, DESIGN.id, _form(), {})

        assert env.analytics.events[-1].properties["is_first_profile"] is False

    async def test_a_failed_commit_sends_nothing(self, env):
        profile = await env.create()
        env.uow.fail_on_commit = True

        with pytest.raises(RuntimeError):
            await env.create_use_case().execute(await env.user(), TECH.id, DESIGN.id, _form(), {})
        with pytest.raises(RuntimeError):
            await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior"})
        with pytest.raises(RuntimeError):
            await env.pause_use_case().execute(await env.user(), profile.id)

        assert env.analytics.events == []

    async def test_a_duplicate_profile_sends_nothing(self, env):
        await env.create()

        with pytest.raises(ProfileAlreadyExistsError):
            await env.create_use_case().execute(await env.user(), TECH.id, ENGINEERING.id, _form(), {"grade": "junior"})

        assert env.analytics.events == []

    async def test_an_invalid_profile_sends_nothing(self, env):
        with pytest.raises(ProfileTagInvalidError):
            await env.create_use_case().execute(
                await env.user(), TECH.id, ENGINEERING.id, _form(tag_ids=[1, 2, 3, 4, 9]), {"grade": "junior"}
            )

        assert env.analytics.events == []

    @pytest.mark.parametrize(
        "form_overrides, attributes, flag",
        [
            ({"country_code": "AT", "timezone": "Europe/Vienna"}, {"grade": "junior"}, None),
            ({"timezone": "Europe/Busingen"}, {"grade": "junior"}, "timezone_changed"),
            ({"bio": "y" * 300}, {"grade": "junior"}, "bio_changed"),
            ({"goals_description": "y" * 300}, {"grade": "junior"}, "goals_changed"),
            ({"tag_ids": [1, 2, 3, 4, 6]}, {"grade": "junior"}, "tags_changed"),
            ({}, {"grade": "senior"}, "attributes_changed"),
        ],
        ids=["country", "timezone", "bio", "goals", "tags", "attributes"],
    )
    async def test_an_update_flags_exactly_the_fields_that_changed(self, env, form_overrides, attributes, flag):
        profile = await env.create()

        await env.update_use_case().execute(1, profile.id, _form(**form_overrides), attributes)

        [event] = env.analytics.events
        assert event.event is AnalyticsEvent.PROFILE_UPDATED
        # A new country always comes with a zone of that country, so both flags are set.
        expected = (
            {**NO_FIELD_CHANGED, "country_changed": True, "timezone_changed": True}
            if flag is None
            else {**NO_FIELD_CHANGED, flag: True}
        )
        assert {key: event.properties[key] for key in NO_FIELD_CHANGED} == expected
        assert (event.properties["actor_profile_id"], event.properties["tags_count"]) == (profile.id, 5)

    async def test_the_same_tags_in_another_order_are_not_a_change(self, env):
        profile = await env.create()

        await env.update_use_case().execute(1, profile.id, _form(tag_ids=[5, 4, 3, 2, 1]), {"grade": "junior"})

        assert env.analytics.events[0].properties["tags_changed"] is False

    async def test_an_unchanged_put_is_still_saved_and_reported_with_every_flag_false(self, env):
        profile = await env.create()

        await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior"})

        [event] = env.analytics.events
        assert {key: event.properties[key] for key in NO_FIELD_CHANGED} == NO_FIELD_CHANGED
        assert env.uow.commits == 1
        assert env.queue.enqueued == [profile.id]

    async def test_an_update_of_a_hidden_profile_sends_nothing(self, env):
        profile = await env.create()
        await env.profiles.set_status(profile.id, ProfileStatus.HIDDEN_BY_ADMIN)

        with pytest.raises(ProfileHiddenError):
            await env.update_use_case().execute(1, profile.id, _form(), {"grade": "junior"})

        assert env.analytics.events == []

    async def test_switching_the_active_profile_is_reported_and_a_repeat_is_not(self, env):
        await env.create()
        second = await env.create(role=DESIGN)

        await env.activate_use_case().execute(await env.user(), second.id)
        await env.activate_use_case().execute(await env.user(), second.id)

        [event] = env.analytics.events
        assert event.event is AnalyticsEvent.PROFILE_ACTIVATED
        assert event.properties == {"actor_profile_id": second.id, "category_id": TECH.id, "role_id": DESIGN.id}

    async def test_a_pause_and_a_resume_are_reported_once_each_and_their_repeats_are_not(self, env):
        profile = await env.create()

        await env.pause_use_case().execute(await env.user(), profile.id)
        await env.pause_use_case().execute(await env.user(), profile.id)
        await env.resume_use_case().execute(await env.user(), profile.id)
        await env.resume_use_case().execute(await env.user(), profile.id)

        assert [event.event for event in env.analytics.events] == [
            AnalyticsEvent.PROFILE_PAUSED,
            AnalyticsEvent.PROFILE_RESUMED,
        ]
        assert all(event.properties["actor_profile_id"] == profile.id for event in env.analytics.events)

    async def test_a_deleted_profile_is_reported_with_what_it_was(self, env):
        profile = await env.create()

        await env.delete_use_case().execute(await env.user(), profile.id)

        [event] = env.analytics.events
        assert event.event is AnalyticsEvent.PROFILE_DELETED
        assert event.properties == {"actor_profile_id": profile.id, "category_id": TECH.id, "role_id": ENGINEERING.id}

    async def test_somebody_elses_profile_sends_nothing(self, env):
        profile = await env.create()

        for use_case in (env.pause_use_case(), env.resume_use_case(), env.activate_use_case(), env.delete_use_case()):
            with pytest.raises(ProfileNotFoundError):
                await use_case.execute(await env.user(2), profile.id)

        assert env.analytics.events == []
