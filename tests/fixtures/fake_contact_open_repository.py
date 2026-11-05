from dataclasses import replace
from datetime import datetime

from app.domain.entities import ContactNotificationContext, ContactOpenEntity, ContactWindowUsage, FeedViewer
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
        self._contexts: dict[tuple[int, int], ContactNotificationContext] = {}
        self.add_calls = 0
        # Plays a parallel request that committed the same pair between the use case's check and its insert.
        self.pair_appears_after_exists_checks: int | None = None
        self._exists_calls = 0

    def hide(self, profile_id: int) -> None:
        del self.usernames[profile_id]

    @property
    def opens(self) -> list[ContactOpenEntity]:
        return [replace(o) for o in self._opens.values()]

    def seed_open(
        self,
        opener_profile_id: int,
        opened_profile_id: int,
        created_at: datetime,
        status: NotificationStatus = NotificationStatus.PENDING,
        updated_at: datetime | None = None,
    ) -> None:
        self._opens[(opener_profile_id, opened_profile_id)] = ContactOpenEntity(
            opener_profile_id, opened_profile_id, created_at, status, updated_at or created_at
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

    # ---------- notifications ----------

    def set_context(self, opener_profile_id: int, opened_profile_id: int, context: ContactNotificationContext) -> None:
        """What `get_notification_context` returns for the pair (the facts about both people at send time)."""
        self._contexts[(opener_profile_id, opened_profile_id)] = context

    def status_of(self, opener_profile_id: int, opened_profile_id: int) -> NotificationStatus:
        return self._opens[(opener_profile_id, opened_profile_id)].notification_status

    async def get_notification_context(
        self, opener_profile_id: int, opened_profile_id: int
    ) -> ContactNotificationContext | None:
        pair = (opener_profile_id, opened_profile_id)
        context = self._contexts.get(pair)
        if pair not in self._opens or context is None:
            return None
        # The status is the row's own, the rest is whatever the test set.
        return replace(context, notification_status=self._opens[pair].notification_status)

    async def settle_notification(
        self, opener_profile_id: int, opened_profile_id: int, status: NotificationStatus, now: datetime
    ) -> bool:
        row = self._opens.get((opener_profile_id, opened_profile_id))
        if row is None or row.notification_status is not NotificationStatus.PENDING:
            return False
        row.notification_status = status
        row.notification_updated_at = now
        return True

    async def claim_stale_pending(self, pending_before: datetime, now: datetime, limit: int) -> list[tuple[int, int]]:
        stale = sorted(
            (
                row
                for row in self._opens.values()
                if row.notification_status is NotificationStatus.PENDING and row.notification_updated_at < pending_before
            ),
            key=lambda row: (row.notification_updated_at, row.opener_profile_id),
        )[:limit]
        for row in stale:
            row.notification_updated_at = now
        return sorted((row.opener_profile_id, row.opened_profile_id) for row in stale)
