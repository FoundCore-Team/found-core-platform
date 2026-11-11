from datetime import datetime, timedelta, timezone

from app.application.services.feed_viewer_resolver import FeedViewerResolver
from app.domain.entities import ContactNotificationContext, OpenedContact, UserEntity
from app.domain.enums import NotificationStatus
from app.domain.exceptions import (
    ContactLimitReachedError,
    ContactProfileNotFoundError,
    NotificationRejectedError,
    NotificationUnavailableError,
)
from app.domain.interfaces import (
    IContactOpenRepository,
    INotificationJobQueue,
    INotificationSender,
    IUnitOfWork,
    IUserRepository,
)
from app.domain.services import build_contact_notification_text


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
        notification_queue: INotificationJobQueue,
        unit_of_work: IUnitOfWork,
        daily_limit: int,
        window: timedelta,
    ) -> None:
        self._viewer_resolver = viewer_resolver
        self._contact_open_repository = contact_open_repository
        self._user_repository = user_repository
        self._notification_queue = notification_queue
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
        # After the commit, so the worker finds the row. Never raises: if the broker is down the row stays `pending`
        # and the sweep schedules it later.
        await self._notification_queue.enqueue(viewer.profile_id, profile_id)
        return OpenedContact(
            username=username,
            is_new_open=True,
            remaining_today=self._daily_limit - usage.count - 1,
        )


class SendContactNotificationUseCase:
    """The worker's job: tells the owner of a profile that somebody opened their contact.

    It decides on the facts as they are now, not as they were at the open: the job may run late (retries, sweep).
    """

    def __init__(
        self,
        contact_open_repository: IContactOpenRepository,
        notification_sender: INotificationSender,
        unit_of_work: IUnitOfWork,
        stale_after: timedelta,
    ) -> None:
        self._contact_open_repository = contact_open_repository
        self._notification_sender = notification_sender
        self._unit_of_work = unit_of_work
        self._stale_after = stale_after

    @staticmethod
    def _should_skip(context: ContactNotificationContext) -> bool:
        # A pause does not stop the message: the owner still wants to know. A ban or a deleted account does, and so
        # does an opener who lost their username (the message would have nothing to write to). A profile hidden by an
        # admin stops the message too, otherwise the violator could send their name, `@username` and a link to the
        # hidden profile to other people through the bot.
        return (
            context.opener_is_banned
            or context.opener_is_deleted
            or context.opener_profile_is_hidden
            or not context.notification.opener_username
            or context.owner_is_banned
            or context.owner_is_deleted
        )

    async def _settle(self, opener_profile_id: int, opened_profile_id: int, status: NotificationStatus) -> None:
        await self._contact_open_repository.settle_notification(
            opener_profile_id, opened_profile_id, status, datetime.now(timezone.utc)
        )
        await self._unit_of_work.commit()

    async def execute(self, opener_profile_id: int, opened_profile_id: int, is_last_attempt: bool) -> None:
        context = await self._contact_open_repository.get_notification_context(opener_profile_id, opened_profile_id)
        if context is None:
            # A profile was deleted while the job was waiting; the row went with it (cascade).
            return
        if context.notification_status is not NotificationStatus.PENDING:
            # A duplicate job (retry racing the sweep): somebody already settled this one.
            return

        if datetime.now(timezone.utc) - context.created_at > self._stale_after:
            # News that is a day late reads like spam, especially a whole batch of it after a long outage.
            await self._settle(opener_profile_id, opened_profile_id, NotificationStatus.SKIPPED)
            return
        if self._should_skip(context):
            await self._settle(opener_profile_id, opened_profile_id, NotificationStatus.SKIPPED)
            return

        text = build_contact_notification_text(context.notification)
        try:
            await self._notification_sender.send_contact_notification(context.notification, text)
        except NotificationUnavailableError:
            if not is_last_attempt:
                raise
            # The retries are used up: final, the sweep does not look at `failed`.
            await self._settle(opener_profile_id, opened_profile_id, NotificationStatus.FAILED)
            return
        except NotificationRejectedError:
            # The user blocked the bot or never started it, or the request is bad: a retry cannot help.
            await self._settle(opener_profile_id, opened_profile_id, NotificationStatus.FAILED)
            return
        await self._settle(opener_profile_id, opened_profile_id, NotificationStatus.SENT)


class SweepStaleNotificationsUseCase:
    """Re-enqueues notifications whose job was lost (the broker was down at the open, a worker died)."""

    def __init__(
        self,
        contact_open_repository: IContactOpenRepository,
        notification_queue: INotificationJobQueue,
        unit_of_work: IUnitOfWork,
        pending_after: timedelta,
        batch_size: int,
    ) -> None:
        self._contact_open_repository = contact_open_repository
        self._notification_queue = notification_queue
        self._unit_of_work = unit_of_work
        self._pending_after = pending_after
        self._batch_size = batch_size

    async def execute(self) -> int:
        now = datetime.now(timezone.utc)
        pairs = await self._contact_open_repository.claim_stale_pending(
            pending_before=now - self._pending_after, now=now, limit=self._batch_size
        )
        # Committed before enqueueing, so the claim is visible to a concurrent sweep and to the jobs themselves.
        await self._unit_of_work.commit()
        for opener_profile_id, opened_profile_id in pairs:
            await self._notification_queue.enqueue(opener_profile_id, opened_profile_id)
        return len(pairs)
