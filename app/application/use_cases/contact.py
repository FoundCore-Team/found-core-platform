from datetime import datetime, timedelta, timezone

from app.application.services.feed_viewer_resolver import FeedViewerResolver
from app.domain.entities import OpenedContact, UserEntity
from app.domain.exceptions import ContactLimitReachedError, ContactProfileNotFoundError
from app.domain.interfaces import IContactOpenRepository, IUnitOfWork, IUserRepository


class OpenContactUseCase:
    """Opens the contact of a profile for the viewer's active profile and hands out the owner's `username`.

    The pair (my active profile -> their profile) is opened once: the first time it is recorded and spends one of the
    daily limit, a repeat gives the `username` again for free. See CLAUDE.md, "Contacts".
    """

    def __init__(
        self,
        viewer_resolver: FeedViewerResolver,
        contact_open_repository: IContactOpenRepository,
        user_repository: IUserRepository,
        unit_of_work: IUnitOfWork,
        daily_limit: int,
        window: timedelta,
    ) -> None:
        self._viewer_resolver = viewer_resolver
        self._contact_open_repository = contact_open_repository
        self._user_repository = user_repository
        self._unit_of_work = unit_of_work
        self._daily_limit = daily_limit
        self._window = window

    async def _repeat(self, username: str, user_id: int, now: datetime) -> OpenedContact:
        usage = await self._contact_open_repository.get_window_usage(user_id, now - self._window)
        return OpenedContact(
            username=username,
            is_new_open=False,
            remaining_today=max(self._daily_limit - usage.count, 0),
        )

    async def execute(self, user: UserEntity, profile_id: int) -> OpenedContact:
        # Raises NoActiveProfileError; the router has already checked the username gate.
        _, viewer = await self._viewer_resolver.resolve(user)
        now = datetime.now(timezone.utc)

        # Visibility and the username in one query. A profile that is the viewer's own, missing, paused, banned or
        # without a username is the same 404, also for a pair that was opened before it became invisible.
        username = await self._contact_open_repository.get_visible_contact(viewer, profile_id)
        if username is None:
            raise ContactProfileNotFoundError()

        # A repeat is free: no lock, no limit check, so it also works when the limit is exhausted.
        if await self._contact_open_repository.exists(viewer.profile_id, profile_id):
            return await self._repeat(username, user.id, now)

        # Opens of one person are serialized from here to the commit, otherwise two parallel opens at the 19th contact
        # would both count 19 and both pass.
        await self._user_repository.lock_for_update(user.id)

        # The same pair may have been committed by a parallel request while this one waited for the lock. Without this
        # check a double click at the limit would answer 429 instead of the free repeat.
        if await self._contact_open_repository.exists(viewer.profile_id, profile_id):
            return await self._repeat(username, user.id, now)

        usage = await self._contact_open_repository.get_window_usage(user.id, now - self._window)
        if usage.count >= self._daily_limit:
            # `oldest_opened_at` is set whenever `count` is positive, which the limit (>= 1) guarantees here.
            assert usage.oldest_opened_at is not None
            raise ContactLimitReachedError(self._daily_limit, usage.oldest_opened_at + self._window)

        opened = await self._contact_open_repository.add(viewer.profile_id, profile_id, now)
        if opened is None:
            # Cannot normally happen after the check under the lock; if it does, it is a repeat all the same.
            return await self._repeat(username, user.id, now)

        await self._unit_of_work.commit()
        return OpenedContact(
            username=username,
            is_new_open=True,
            remaining_today=self._daily_limit - usage.count - 1,
        )
