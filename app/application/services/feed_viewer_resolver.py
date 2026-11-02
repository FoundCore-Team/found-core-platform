from app.domain.entities import FeedViewer, ProfileEntity, UserEntity
from app.domain.enums import ProfileStatus, TagStatus
from app.domain.exceptions import NoActiveProfileError
from app.domain.interfaces import IProfileEmbeddingRepository, IProfileRepository


class FeedViewerResolver:
    """Turns the authenticated user into the viewer every feed endpoint ranks relative to: the active profile."""

    def __init__(
        self,
        profile_repository: IProfileRepository,
        profile_embedding_repository: IProfileEmbeddingRepository,
        current_embedding_model: str,
    ) -> None:
        self._profile_repository = profile_repository
        self._profile_embedding_repository = profile_embedding_repository
        self._current_embedding_model = current_embedding_model

    async def resolve(self, user: UserEntity) -> tuple[ProfileEntity, FeedViewer]:
        profile = None
        if user.active_profile_id is not None:
            profile = await self._profile_repository.get_by_id(user.active_profile_id)
        # A profile that is no longer active (paused, hidden by moderation) cannot drive the feed.
        if profile is None or profile.user_id != user.id or profile.status is not ProfileStatus.ACTIVE:
            raise NoActiveProfileError()

        embedding = await self._profile_embedding_repository.get(profile.id)
        viewer = FeedViewer(
            profile_id=profile.id,
            user_id=user.id,
            category_id=profile.category_id,
            role_id=profile.role_id,
            country_code=profile.country_code,
            timezone=profile.timezone,
            tag_ids=[tag.id for tag in profile.tags if tag.status is not TagStatus.REJECTED],
            # `model` is set if and only if a vector is stored; vectors of another model are not comparable.
            has_comparable_vector=embedding is not None and embedding.model == self._current_embedding_model,
        )
        return profile, viewer
