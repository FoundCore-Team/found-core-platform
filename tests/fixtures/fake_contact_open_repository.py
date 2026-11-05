from dataclasses import replace
from datetime import datetime

from app.domain.entities import ContactOpenEntity, ContactWindowUsage, FeedViewer
from app.domain.enums import NotificationStatus
from app.domain.interfaces import IContactOpenRepository


class FakeContactOpenRepository(IContactOpenRepository):
    """In-memory stand-in for SqlAlchemyContactOpenRepository. Implements the real ABC.

    Visibility is modelled by `usernames`: profile id -> username of its owner. A profile that is absent there is not
    visible (own, paused, banned, no username...); `hide()` plays a profile that became invisible meanwhile.
    The window is counted per user through `owner_of_opener` (opener profile id -> user id), as the real query joins
    the opener's profile to its owner.
    """

    def __init__(self, usernames: dict[int, str], owner_of_opener: dict[int, int]) -> None:
        self.usernames = dict(usernames)
        self.owner_of_opener = dict(owner_of_opener)
        self._opens: dict[tuple[int, int], ContactOpenEntity] = {}
        self.add_calls = 0
        # Plays a parallel request that committed the same pair between the use case's check and its insert.
        self.pair_appears_after_exists_checks: int | None = None
        self._exists_calls = 0

    def hide(self, profile_id: int) -> None:
        del self.usernames[profile_id]

    @property
    def opens(self) -> list[ContactOpenEntity]:
        return [replace(o) for o in self._opens.values()]

    def seed_open(self, opener_profile_id: int, opened_profile_id: int, created_at: datetime) -> None:
        self._opens[(opener_profile_id, opened_profile_id)] = ContactOpenEntity(
            opener_profile_id, opened_profile_id, created_at, NotificationStatus.PENDING, created_at
        )

    async def get_visible_contact(self, viewer: FeedViewer, profile_id: int) -> str | None:
        return self.usernames.get(profile_id)

    async def exists(self, opener_profile_id: int, opened_profile_id: int) -> bool:
        self._exists_calls += 1
        if (
            self.pair_appears_after_exists_checks is not None
            and self._exists_calls > self.pair_appears_after_exists_checks
        ):
            self.seed_open(opener_profile_id, opened_profile_id, datetime.now().astimezone())
        return (opener_profile_id, opened_profile_id) in self._opens

    async def get_window_usage(self, user_id: int, since: datetime) -> ContactWindowUsage:
        created = [
            o.created_at
            for o in self._opens.values()
            if self.owner_of_opener[o.opener_profile_id] == user_id and o.created_at > since
        ]
        return ContactWindowUsage(count=len(created), oldest_opened_at=min(created) if created else None)

    async def add(self, opener_profile_id: int, opened_profile_id: int, now: datetime) -> ContactOpenEntity | None:
        self.add_calls += 1
        if (opener_profile_id, opened_profile_id) in self._opens:
            return None
        entity = ContactOpenEntity(opener_profile_id, opened_profile_id, now, NotificationStatus.PENDING, now)
        self._opens[(opener_profile_id, opened_profile_id)] = entity
        return replace(entity)
