from dataclasses import replace

from app.domain.entities import NewProfileEntity, ProfileEntity, TagEntity
from app.domain.enums import ProfileStatus
from app.domain.interfaces import IProfileRepository


class FakeProfileRepository(IProfileRepository):
    """In-memory stand-in for SqlAlchemyProfileRepository. Implements the real
    ABC so a signature drift there fails the unit tests too.

    Like the real `create` (`INSERT ... ON CONFLICT DO NOTHING`), it returns None
    when the (user, category, role) profile already exists. `lose_race_once`
    simulates a concurrent request winning the INSERT: the next `create` stores
    the profile as if another request had inserted it first and returns None.

    `known_tags` plays the role of the tags table: a tag id outside of it behaves
    like a foreign key violation (KeyError).
    """

    def __init__(
        self,
        profiles: list[ProfileEntity] | None = None,
        known_tags: list[TagEntity] | None = None,
    ) -> None:
        self._known_tags: dict[int, TagEntity] = {t.id: replace(t) for t in (known_tags or [])}
        self._profiles: dict[int, ProfileEntity] = {p.id: self._copy(p) for p in (profiles or [])}
        self._next_id = max(self._profiles, default=0) + 1
        self.lose_race_once = False

    @staticmethod
    def _copy(profile: ProfileEntity) -> ProfileEntity:
        return replace(
            profile,
            extra_attributes=dict(profile.extra_attributes),
            tags=[replace(t) for t in profile.tags],
        )

    def _tags_by_ids(self, tag_ids: list[int]) -> list[TagEntity]:
        return [replace(self._known_tags[tag_id]) for tag_id in sorted(tag_ids)]

    def _store(self, profile: NewProfileEntity) -> ProfileEntity:
        entity = ProfileEntity(
            id=self._next_id,
            user_id=profile.user_id,
            category_id=profile.category_id,
            role_id=profile.role_id,
            country_code=profile.country_code,
            timezone=profile.timezone,
            bio=profile.bio,
            goals_description=profile.goals_description,
            extra_attributes=dict(profile.extra_attributes),
            status=profile.status,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            tags=self._tags_by_ids(profile.tag_ids),
        )
        self._profiles[entity.id] = entity
        self._next_id += 1
        return entity

    @property
    def profiles(self) -> list[ProfileEntity]:
        return [self._copy(p) for p in self._profiles.values()]

    async def get_by_id(self, profile_id: int) -> ProfileEntity | None:
        profile = self._profiles.get(profile_id)
        return self._copy(profile) if profile else None

    async def get_by_user_category_role(
        self, user_id: int, category_id: int, role_id: int
    ) -> ProfileEntity | None:
        for profile in self._profiles.values():
            if (profile.user_id, profile.category_id, profile.role_id) == (user_id, category_id, role_id):
                return self._copy(profile)
        return None

    async def list_by_user_id(self, user_id: int) -> list[ProfileEntity]:
        own = [p for p in self._profiles.values() if p.user_id == user_id]
        return [self._copy(p) for p in sorted(own, key=lambda p: (p.created_at, p.id))]

    async def get_newest_active_by_user_id(self, user_id: int) -> ProfileEntity | None:
        active = [
            p for p in self._profiles.values() if p.user_id == user_id and p.status == ProfileStatus.ACTIVE
        ]
        if not active:
            return None
        return self._copy(max(active, key=lambda p: (p.created_at, p.id)))

    async def create(self, profile: NewProfileEntity) -> ProfileEntity | None:
        exists = (
            await self.get_by_user_category_role(profile.user_id, profile.category_id, profile.role_id)
            is not None
        )
        if self.lose_race_once and not exists:
            self.lose_race_once = False
            self._store(profile)  # the other request's insert
            return None
        if exists:
            return None
        return self._copy(self._store(profile))

    async def update(self, profile: ProfileEntity, tag_ids: list[int]) -> ProfileEntity:
        # Same columns as the real repository: status, category and role are never written here.
        stored = self._profiles[profile.id]
        stored.country_code = profile.country_code
        stored.timezone = profile.timezone
        stored.bio = profile.bio
        stored.goals_description = profile.goals_description
        stored.extra_attributes = dict(profile.extra_attributes)
        stored.updated_at = profile.updated_at
        stored.tags = self._tags_by_ids(tag_ids)
        return self._copy(stored)

    async def set_status(self, profile_id: int, status: ProfileStatus) -> bool:
        # Like the real UPDATE: a hidden (or missing) profile is left alone and False is returned.
        profile = self._profiles.get(profile_id)
        if profile is None or profile.status == ProfileStatus.HIDDEN_BY_ADMIN:
            return False
        profile.status = status
        return True

    async def hide(self, profile_id: int) -> bool:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.status == ProfileStatus.HIDDEN_BY_ADMIN:
            return False
        profile.status = ProfileStatus.HIDDEN_BY_ADMIN
        return True

    async def unhide(self, profile_id: int) -> bool:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.status != ProfileStatus.HIDDEN_BY_ADMIN:
            return False
        profile.status = ProfileStatus.PAUSED
        return True

    async def delete(self, profile_id: int) -> None:
        self._profiles.pop(profile_id, None)
