"""Approve / reject / merge of custom tags, the alias handling around them, and the profile validator's view of
merged tags, with in-memory fakes. The SQL, the locks and the queue are checked on real Postgres elsewhere."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.application.services.profile_content_validator import ProfileContentValidator
from app.application.use_cases import ApproveTagUseCase, CreateCustomTagUseCase, MergeTagUseCase, RejectTagUseCase
from app.core.config import ProfileConfig, TaxonomyConfig
from app.domain.entities import CategoryEntity, NewTagScopeEntity, ProfileFormEntity, RoleEntity, TagEntity
from app.domain.enums import AdminActionType, AdminTargetType, EmbeddingStatus, TagMergeTargetProblem, TagStatus
from app.domain.exceptions import (
    ProfileTagInvalidError,
    TagMergeTargetInvalidError,
    TagNotFoundError,
    TagRejectedError,
    TagStatusConflictError,
)
from app.domain.services import ExtraAttributesValidator, ProfileFormValidator, TagTitleValidator, normalize_tag_title
from tests.fixtures.factories import make_profile_entity, make_user_entity
from tests.fixtures.fake_admin_action_repository import FakeAdminActionRepository
from tests.fixtures.fake_analytics import FakeAnalyticsTracker
from tests.fixtures.fake_profile_embedding_repository import FakeProfileEmbeddingRepository
from tests.fixtures.fake_profile_repository import FakeProfileRepository
from tests.fixtures.fake_taxonomy import FakeTaxonomyRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork

ADMIN = make_user_entity(id=100, telegram_id=1000, is_admin=True)
TECH_ID, EDU_ID = 1, 2
ENGINEERING_ID = 10
OLD_UPDATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _tag(
    tag_id: int,
    title: str,
    status: TagStatus = TagStatus.APPROVED,
    *,
    usage_count: int = 0,
    merged_into: int | None = None,
) -> TagEntity:
    return TagEntity(
        id=tag_id,
        title=title,
        normalized_title=normalize_tag_title(title),
        status=status,
        created_by_user_id=None,
        usage_count=usage_count,
        merged_into_tag_id=merged_into,
    )


def _profile(profile_id: int, tags: list[TagEntity]):
    return make_profile_entity(id=profile_id, user_id=profile_id, updated_at=OLD_UPDATED_AT, tags=tags)


class Env:
    def __init__(self, tags: list[TagEntity], profiles_tags: dict[int, list[int]] | None = None) -> None:
        self.taxonomy = FakeTaxonomyRepository(tags=[replace(t) for t in tags])
        by_id = {t.id: t for t in tags}
        self.profiles = FakeProfileRepository(
            [_profile(pid, [by_id[i] for i in tag_ids]) for pid, tag_ids in (profiles_tags or {}).items()],
            known_tags=tags,
        )
        self.embeddings = FakeProfileEmbeddingRepository()
        self.journal = FakeAdminActionRepository()
        self.uow = FakeUnitOfWork()

    def approve(self) -> ApproveTagUseCase:
        return ApproveTagUseCase(self.taxonomy, self.journal, self.uow)

    def reject(self) -> RejectTagUseCase:
        return RejectTagUseCase(self.taxonomy, self.profiles, self.embeddings, self.journal, self.uow)

    def merge(self) -> MergeTagUseCase:
        return MergeTagUseCase(self.taxonomy, self.profiles, self.embeddings, self.journal, self.uow)

    def profile_tag_ids(self, profile_id: int) -> list[int]:
        profile = next(p for p in self.profiles.profiles if p.id == profile_id)
        return [t.id for t in profile.tags]

    def profile_updated_at(self, profile_id: int) -> datetime:
        return next(p for p in self.profiles.profiles if p.id == profile_id).updated_at


class TestApprove:
    async def test_pending_becomes_approved_with_one_journal_row_and_one_commit(self):
        env = Env([_tag(1, "Rust", TagStatus.PENDING)])

        result = await env.approve().execute(ADMIN, 1, "looks fine")

        assert result.tag.status is TagStatus.APPROVED
        assert result.affected_profiles == 0
        assert env.taxonomy.tags[1].status is TagStatus.APPROVED
        assert env.uow.commits == 1
        (action,) = env.journal.actions
        assert (action.actor_user_id, action.action, action.target_type, action.target_id, action.reason) == (
            ADMIN.id,
            AdminActionType.APPROVE_TAG,
            AdminTargetType.TAG,
            1,
            "looks fine",
        )
        assert action.details == {"previous_status": "pending"}

    async def test_approving_an_approved_tag_changes_nothing(self):
        env = Env([_tag(1, "Rust")])

        result = await env.approve().execute(ADMIN, 1, "again")

        assert result.tag.status is TagStatus.APPROVED
        assert env.journal.actions == []
        assert env.uow.commits == 0

    @pytest.mark.parametrize("status", [TagStatus.REJECTED, TagStatus.MERGED])
    async def test_other_statuses_are_a_conflict(self, status):
        merged_into = 2 if status is TagStatus.MERGED else None
        env = Env([_tag(1, "Rust", status, merged_into=merged_into), _tag(2, "Rust lang")])

        with pytest.raises(TagStatusConflictError) as error:
            await env.approve().execute(ADMIN, 1, "no")

        assert error.value.status is status
        assert env.taxonomy.tags[1].status is status
        assert env.journal.actions == []
        assert env.uow.commits == 0

    async def test_unknown_tag(self):
        env = Env([])

        with pytest.raises(TagNotFoundError):
            await env.approve().execute(ADMIN, 1, "no")

        assert env.uow.commits == 0

    async def test_a_failing_journal_write_commits_nothing(self):
        env = Env([_tag(1, "Rust", TagStatus.PENDING)])
        env.journal.fail_on_add = RuntimeError("journal down")

        with pytest.raises(RuntimeError):
            await env.approve().execute(ADMIN, 1, "x")

        assert env.uow.commits == 0


class TestReject:
    async def test_tag_is_removed_from_profiles_and_their_embeddings_are_queued(self):
        env = Env(
            [_tag(1, "Spam", TagStatus.PENDING), _tag(2, "Python")],
            {1: [1, 2], 2: [1], 3: [2]},
        )

        result = await env.reject().execute(ADMIN, 1, "spam")

        assert result.tag.status is TagStatus.REJECTED
        assert result.affected_profiles == 2
        assert env.taxonomy.tags[1].status is TagStatus.REJECTED
        assert env.profile_tag_ids(1) == [2]
        assert env.profile_tag_ids(2) == []
        assert env.profile_tag_ids(3) == [2]
        # Touched profiles get one new updated_at; the untouched one keeps the old value.
        assert env.profile_updated_at(1) == env.profile_updated_at(2) > OLD_UPDATED_AT
        assert env.profile_updated_at(3) == OLD_UPDATED_AT
        rows = {r.profile_id: r for r in env.embeddings.rows}
        assert set(rows) == {1, 2}
        assert all(r.status is EmbeddingStatus.PENDING for r in rows.values())
        assert rows[1].source_updated_at == env.profile_updated_at(1)
        assert env.uow.commits == 1
        (action,) = env.journal.actions
        assert action.action is AdminActionType.REJECT_TAG
        assert action.details == {"previous_status": "pending", "detached_profiles": 2}

    async def test_an_approved_tag_can_be_rejected(self):
        env = Env([_tag(1, "Spam")], {1: [1]})

        result = await env.reject().execute(ADMIN, 1, "was a mistake")

        assert result.tag.status is TagStatus.REJECTED
        assert env.journal.actions[0].details["previous_status"] == "approved"

    async def test_a_tag_without_profiles_touches_no_embeddings(self):
        env = Env([_tag(1, "Spam", TagStatus.PENDING)])

        result = await env.reject().execute(ADMIN, 1, "spam")

        assert result.affected_profiles == 0
        assert env.embeddings.rows == []
        assert env.uow.commits == 1

    async def test_rejecting_a_rejected_tag_changes_nothing(self):
        env = Env([_tag(1, "Spam", TagStatus.REJECTED)])

        result = await env.reject().execute(ADMIN, 1, "again")

        assert result.tag.status is TagStatus.REJECTED
        assert env.journal.actions == []
        assert env.uow.commits == 0

    async def test_a_merged_tag_cannot_be_rejected(self):
        env = Env([_tag(1, "NodeJS", TagStatus.MERGED, merged_into=2), _tag(2, "Node.js")])

        with pytest.raises(TagStatusConflictError):
            await env.reject().execute(ADMIN, 1, "no")

        assert env.uow.commits == 0

    async def test_unknown_tag(self):
        env = Env([])

        with pytest.raises(TagNotFoundError):
            await env.reject().execute(ADMIN, 1, "no")


class TestMerge:
    async def test_profiles_scopes_usage_and_alias_are_all_moved(self):
        env = Env(
            [
                _tag(1, "NodeJS", TagStatus.PENDING, usage_count=2),
                _tag(2, "Node.js", usage_count=5),
                _tag(3, "Python"),
            ],
            {1: [1, 3], 2: [1, 2], 3: [3]},
        )
        env.taxonomy.scopes = [
            NewTagScopeEntity(tag_id=1, category_id=TECH_ID, role_id=ENGINEERING_ID),
            NewTagScopeEntity(tag_id=1, category_id=EDU_ID, role_id=None),
            NewTagScopeEntity(tag_id=2, category_id=TECH_ID, role_id=ENGINEERING_ID),
        ]

        result = await env.merge().execute(ADMIN, 1, 2, "same thing")

        assert (result.tag.id, result.tag.status, result.tag.merged_into_tag_id) == (1, TagStatus.MERGED, 2)
        assert result.affected_profiles == 2
        assert env.taxonomy.tags[2].usage_count == 7
        assert env.taxonomy.tags[2].status is TagStatus.APPROVED
        assert sorted((s.tag_id, s.category_id, s.role_id or 0) for s in env.taxonomy.scopes) == [
            (1, TECH_ID, ENGINEERING_ID),
            (1, EDU_ID, 0),
            (2, TECH_ID, ENGINEERING_ID),
            (2, EDU_ID, 0),
        ]
        assert env.profile_tag_ids(1) == [2, 3]
        assert env.profile_tag_ids(2) == [2]  # had both: one link remains
        assert env.profile_tag_ids(3) == [3]
        assert env.profile_updated_at(1) == env.profile_updated_at(2) > OLD_UPDATED_AT
        assert env.profile_updated_at(3) == OLD_UPDATED_AT
        assert {r.profile_id for r in env.embeddings.rows} == {1, 2}
        assert env.uow.commits == 1
        (action,) = env.journal.actions
        assert (action.action, action.target_type, action.target_id) == (
            AdminActionType.MERGE_TAG,
            AdminTargetType.TAG,
            1,
        )
        assert action.details == {"target_tag_id": 2, "moved_profiles": 2}

    async def test_aliases_of_the_source_are_redirected_to_the_target(self):
        env = Env(
            [
                _tag(1, "NodeJS"),
                _tag(2, "Node.js"),
                _tag(3, "Node Js", TagStatus.MERGED, merged_into=1),
            ]
        )

        await env.merge().execute(ADMIN, 1, 2, "same")

        assert env.taxonomy.tags[3].merged_into_tag_id == 2
        assert env.taxonomy.tags[1].merged_into_tag_id == 2

    async def test_both_rows_are_locked_in_ascending_id_order(self):
        env = Env([_tag(5, "NodeJS"), _tag(2, "Node.js")])

        await env.merge().execute(ADMIN, 5, 2, "same")

        assert env.taxonomy.lock_calls == [[2, 5]]

    async def test_repeating_the_same_merge_changes_nothing(self):
        env = Env([_tag(1, "NodeJS"), _tag(2, "Node.js")])
        await env.merge().execute(ADMIN, 1, 2, "same")
        actions, commits = len(env.journal.actions), env.uow.commits

        result = await env.merge().execute(ADMIN, 1, 2, "same")

        assert result.tag.status is TagStatus.MERGED
        assert result.affected_profiles == 0
        assert (len(env.journal.actions), env.uow.commits) == (actions, commits)
        assert env.taxonomy.tags[2].usage_count == 0

    async def test_merging_into_itself(self):
        env = Env([_tag(1, "Node.js")])

        with pytest.raises(TagMergeTargetInvalidError) as error:
            await env.merge().execute(ADMIN, 1, 1, "no")

        assert error.value.problem is TagMergeTargetProblem.SAME_TAG
        assert env.taxonomy.lock_calls == []

    async def test_unknown_source(self):
        env = Env([_tag(2, "Node.js")])

        with pytest.raises(TagNotFoundError):
            await env.merge().execute(ADMIN, 1, 2, "no")

    async def test_unknown_target(self):
        env = Env([_tag(1, "NodeJS")])

        with pytest.raises(TagMergeTargetInvalidError) as error:
            await env.merge().execute(ADMIN, 1, 2, "no")

        assert error.value.problem is TagMergeTargetProblem.TARGET_NOT_FOUND

    @pytest.mark.parametrize(
        ("target_status", "problem"),
        [
            (TagStatus.PENDING, TagMergeTargetProblem.TARGET_NOT_APPROVED),
            (TagStatus.REJECTED, TagMergeTargetProblem.TARGET_NOT_APPROVED),
            (TagStatus.MERGED, TagMergeTargetProblem.TARGET_IS_ALIAS),
        ],
    )
    async def test_target_must_be_an_approved_tag_and_not_an_alias(self, target_status, problem):
        merged_into = 3 if target_status is TagStatus.MERGED else None
        env = Env(
            [_tag(1, "NodeJS"), _tag(2, "Node.js", target_status, merged_into=merged_into), _tag(3, "Node")]
        )

        with pytest.raises(TagMergeTargetInvalidError) as error:
            await env.merge().execute(ADMIN, 1, 2, "no")

        assert error.value.problem is problem
        assert env.uow.commits == 0
        assert env.taxonomy.tags[1].status is TagStatus.APPROVED

    async def test_source_already_merged_elsewhere_is_a_conflict(self):
        env = Env([_tag(1, "NodeJS", TagStatus.MERGED, merged_into=3), _tag(2, "Node.js"), _tag(3, "Node")])

        with pytest.raises(TagStatusConflictError):
            await env.merge().execute(ADMIN, 1, 2, "no")

    async def test_rejected_source_is_a_conflict(self):
        env = Env([_tag(1, "NodeJS", TagStatus.REJECTED), _tag(2, "Node.js")])

        with pytest.raises(TagStatusConflictError):
            await env.merge().execute(ADMIN, 1, 2, "no")

    async def test_a_failing_journal_write_commits_nothing(self):
        env = Env([_tag(1, "NodeJS"), _tag(2, "Node.js")])
        env.journal.fail_on_add = RuntimeError("journal down")

        with pytest.raises(RuntimeError):
            await env.merge().execute(ADMIN, 1, 2, "same")

        assert env.uow.commits == 0


class TestCustomTagFollowsAnAlias:
    def _use_case(self, taxonomy: FakeTaxonomyRepository, uow: FakeUnitOfWork) -> CreateCustomTagUseCase:
        taxonomy.categories = [CategoryEntity(id=TECH_ID, slug="tech", title="Tech")]
        taxonomy.roles = [RoleEntity(id=ENGINEERING_ID, category_id=TECH_ID, slug="eng", title="Eng")]
        config = TaxonomyConfig()
        validator = TagTitleValidator(
            min_length=config.tag_title_min_length,
            max_length=config.tag_title_max_length,
            pattern=config.tag_title_allowed_pattern,
        )
        return CreateCustomTagUseCase(taxonomy, validator, uow, FakeAnalyticsTracker())

    async def test_an_alias_title_returns_the_target_and_adds_the_scope_to_it(self):
        env = Env([_tag(1, "NodeJS", TagStatus.MERGED, merged_into=2), _tag(2, "Node.js")])

        tag = await self._use_case(env.taxonomy, env.uow).execute("NODEJS", TECH_ID, ENGINEERING_ID, 7)

        assert (tag.id, tag.title) == (2, "Node.js")
        assert env.taxonomy.scopes == [NewTagScopeEntity(tag_id=2, category_id=TECH_ID, role_id=ENGINEERING_ID)]
        assert len(env.taxonomy.tags) == 2  # no new duplicate
        assert env.uow.commits == 1

    async def test_an_alias_of_a_rejected_target_is_rejected_too(self):
        env = Env([_tag(1, "NodeJS", TagStatus.MERGED, merged_into=2), _tag(2, "Node.js", TagStatus.REJECTED)])

        with pytest.raises(TagRejectedError):
            await self._use_case(env.taxonomy, env.uow).execute("nodejs", TECH_ID, ENGINEERING_ID, 7)

        assert env.taxonomy.scopes == []
        assert env.uow.commits == 0


class TestProfileValidatorAndMergedTags:
    def _validator(self, taxonomy: FakeTaxonomyRepository) -> ProfileContentValidator:
        config = ProfileConfig()
        return ProfileContentValidator(
            taxonomy,
            ProfileFormValidator(
                bio_min_length=config.bio_min_length,
                bio_max_length=config.bio_max_length,
                goals_min_length=config.goals_min_length,
                goals_max_length=config.goals_max_length,
                tags_min_count=config.tags_min_count,
                tags_max_count=config.tags_max_count,
                timezones_by_country={"DE": ["Europe/Berlin"]},
            ),
            ExtraAttributesValidator(),
        )

    def _env_with_tag_five(self, status: TagStatus) -> Env:
        tags = [_tag(i, f"tag-{i}") for i in range(1, 5)]
        tags.append(_tag(5, "tag-5", status, merged_into=1 if status is TagStatus.MERGED else None))
        env = Env(tags)
        env.taxonomy.scopes = [
            NewTagScopeEntity(tag_id=t.id, category_id=TECH_ID, role_id=ENGINEERING_ID) for t in tags
        ]
        return env

    def _form(self) -> ProfileFormEntity:
        return ProfileFormEntity(
            country_code="DE",
            timezone="Europe/Berlin",
            bio="x" * 200,
            goals_description="x" * 200,
            tag_ids=[1, 2, 3, 4, 5],
        )

    async def test_a_merged_tag_id_is_invalid_input(self):
        env = self._env_with_tag_five(TagStatus.MERGED)

        with pytest.raises(ProfileTagInvalidError):
            await self._validator(env.taxonomy).validate(TECH_ID, ENGINEERING_ID, self._form(), {})

    async def test_a_rejected_tag_is_still_a_conflict(self):
        env = self._env_with_tag_five(TagStatus.REJECTED)

        with pytest.raises(TagRejectedError):
            await self._validator(env.taxonomy).validate(TECH_ID, ENGINEERING_ID, self._form(), {})
