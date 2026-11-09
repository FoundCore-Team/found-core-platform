from datetime import datetime, timezone
from typing import Any

from app.domain.entities import NewAdminActionEntity, PendingTagEntity, TagModerationResult, UserEntity
from app.domain.enums import AdminActionType, AdminTargetType, TagMergeTargetProblem, TagStatus
from app.domain.exceptions import TagMergeTargetInvalidError, TagNotFoundError, TagStatusConflictError
from app.domain.interfaces import (
    IAdminActionRepository,
    IProfileEmbeddingRepository,
    IProfileRepository,
    ITaxonomyRepository,
    IUnitOfWork,
)


def _tag_action(
    admin: UserEntity, action: AdminActionType, tag_id: int, reason: str, details: dict[str, Any]
) -> NewAdminActionEntity:
    return NewAdminActionEntity(
        actor_user_id=admin.id,
        action=action,
        target_type=AdminTargetType.TAG,
        target_id=tag_id,
        reason=reason,
        details=details,
        created_at=datetime.now(timezone.utc),
    )


class ListPendingTagsUseCase:
    """The moderation queue: pending tags, oldest first. Read-only: it never commits."""

    def __init__(self, taxonomy_repository: ITaxonomyRepository) -> None:
        self._taxonomy_repository = taxonomy_repository

    async def execute(self, limit: int, offset: int) -> tuple[list[PendingTagEntity], int]:
        """Returns the page and the total number of pending tags."""
        tags = await self._taxonomy_repository.list_pending_tags(limit, offset)
        total = await self._taxonomy_repository.count_pending_tags()
        return tags, total


class ApproveTagUseCase:
    """`pending -> approved`. Nothing about profiles changes, so no embeddings are refreshed."""

    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, admin: UserEntity, tag_id: int, reason: str) -> TagModerationResult:
        # The lock makes a concurrent approve/reject/merge of the same tag wait; the state read here is the final one.
        locked = await self._taxonomy_repository.lock_tags_for_update([tag_id])
        if not locked:
            raise TagNotFoundError()
        tag = locked[0]
        if tag.status == TagStatus.APPROVED:
            return TagModerationResult(tag=tag, affected_profiles=0)
        if tag.status != TagStatus.PENDING:
            raise TagStatusConflictError(tag.status)

        await self._taxonomy_repository.set_tag_status(tag.id, TagStatus.APPROVED)
        await self._admin_action_repository.add(
            _tag_action(admin, AdminActionType.APPROVE_TAG, tag.id, reason, {"previous_status": tag.status.value})
        )
        await self._unit_of_work.commit()
        tag.status = TagStatus.APPROVED
        return TagModerationResult(tag=tag, affected_profiles=0)


class RejectTagUseCase:
    """`pending`/`approved -> rejected`. The tag disappears from every profile that carried it; the tag row stays
    `rejected`, so entering the same title again answers `409`. Affected profiles may drop below the minimum tag count:
    the minimum is only enforced on save."""

    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        profile_repository: IProfileRepository,
        profile_embedding_repository: IProfileEmbeddingRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._profile_repository = profile_repository
        self._profile_embedding_repository = profile_embedding_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, admin: UserEntity, tag_id: int, reason: str) -> TagModerationResult:
        locked = await self._taxonomy_repository.lock_tags_for_update([tag_id])
        if not locked:
            raise TagNotFoundError()
        tag = locked[0]
        if tag.status == TagStatus.REJECTED:
            return TagModerationResult(tag=tag, affected_profiles=0)
        if tag.status == TagStatus.MERGED:
            raise TagStatusConflictError(tag.status)

        # Lock order: tags (above), then profiles (inside detach/touch), then profile_embeddings.
        detached_profile_ids = await self._profile_repository.detach_tag(tag.id)
        await self._taxonomy_repository.set_tag_status(tag.id, TagStatus.REJECTED)
        # A new `updated_at` makes a running embedding job lose its conditional write; the sweep recomputes the vector.
        touched = await self._profile_repository.touch(detached_profile_ids)
        await self._profile_embedding_repository.mark_pending_many(touched)
        await self._admin_action_repository.add(
            _tag_action(
                admin,
                AdminActionType.REJECT_TAG,
                tag.id,
                reason,
                {"previous_status": tag.status.value, "detached_profiles": len(detached_profile_ids)},
            )
        )
        await self._unit_of_work.commit()
        tag.status = TagStatus.REJECTED
        return TagModerationResult(tag=tag, affected_profiles=len(detached_profile_ids))


class MergeTagUseCase:
    """Makes `source` an alias of `target` (`merged`, `merged_into_tag_id`). Profiles move to the target, the target
    gains the source's scopes (otherwise the next save of a moved profile would fail the scope check) and its usage
    count. The alias stays, so an old id or a re-typed title still leads to the target."""

    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        profile_repository: IProfileRepository,
        profile_embedding_repository: IProfileEmbeddingRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._profile_repository = profile_repository
        self._profile_embedding_repository = profile_embedding_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, admin: UserEntity, source_id: int, target_id: int, reason: str) -> TagModerationResult:
        if source_id == target_id:
            raise TagMergeTargetInvalidError(TagMergeTargetProblem.SAME_TAG)

        # Ascending id order for both rows: two merges touching the same pair cannot deadlock.
        locked = await self._taxonomy_repository.lock_tags_for_update(sorted([source_id, target_id]))
        tags_by_id = {tag.id: tag for tag in locked}
        source = tags_by_id.get(source_id)
        if source is None:
            raise TagNotFoundError()
        target = tags_by_id.get(target_id)
        if target is None:
            raise TagMergeTargetInvalidError(TagMergeTargetProblem.TARGET_NOT_FOUND)

        if source.status == TagStatus.MERGED and source.merged_into_tag_id == target.id:
            return TagModerationResult(tag=source, affected_profiles=0)
        if source.status in (TagStatus.MERGED, TagStatus.REJECTED):
            raise TagStatusConflictError(source.status)

        if target.status == TagStatus.MERGED:
            raise TagMergeTargetInvalidError(TagMergeTargetProblem.TARGET_IS_ALIAS)
        if target.status != TagStatus.APPROVED:
            raise TagMergeTargetInvalidError(TagMergeTargetProblem.TARGET_NOT_APPROVED)

        await self._taxonomy_repository.copy_tag_scopes(source.id, target.id)
        moved_profile_ids = await self._profile_repository.replace_tag(source.id, target.id)
        await self._taxonomy_repository.merge_tag(source.id, target.id)
        touched = await self._profile_repository.touch(moved_profile_ids)
        await self._profile_embedding_repository.mark_pending_many(touched)
        await self._admin_action_repository.add(
            _tag_action(
                admin,
                AdminActionType.MERGE_TAG,
                source.id,
                reason,
                {"target_tag_id": target.id, "moved_profiles": len(moved_profile_ids)},
            )
        )
        await self._unit_of_work.commit()

        # merge_tag writes with UPDATE statements, so the new state is read back instead of patched by hand.
        (merged,) = await self._taxonomy_repository.get_tags_by_ids([source.id])
        return TagModerationResult(tag=merged, affected_profiles=len(moved_profile_ids))
