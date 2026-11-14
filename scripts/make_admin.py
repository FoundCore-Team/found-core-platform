"""Grants or revokes the admin role of a user. An operations tool, not a seed: it works in prod too, on purpose (admins
are a handful of people and there is no "become an admin" endpoint to protect).

The user must have logged in at least once (otherwise there is no `users` row). The change is written to the
`admin_actions` journal with no actor: the script is the system. An admin's rights are checked live against the
database on every request, so a revoke takes effect on the next request, without waiting for the token to expire.

Exit code: 0 on success and when nothing had to change, 1 on any error.

Usage:
    python -m scripts.make_admin --telegram-id 123456789 --reason "first admin"
    python -m scripts.make_admin --telegram-id 123456789 --revoke --reason "left the team"
"""

import argparse
import asyncio
import sys

from redis.asyncio import Redis

from app.core.composition.container import Container
from app.core.config import settings
from app.domain.constants import ADMIN_REASON_MAX_LENGTH
from app.domain.exceptions import UserBannedError, UserNotFoundError
from app.infrastructure.helpers import db_helper
from app.infrastructure.providers import NoopAnalyticsTracker


def clean_reason(reason: str) -> str:
    """The same rule as the `reason` of the admin endpoints: trimmed, 1..ADMIN_REASON_MAX_LENGTH, no NUL."""
    cleaned = reason.strip()
    if not cleaned:
        raise ValueError("The reason must not be empty.")
    if len(cleaned) > ADMIN_REASON_MAX_LENGTH:
        raise ValueError(f"The reason must be at most {ADMIN_REASON_MAX_LENGTH} characters.")
    if "\x00" in cleaned:
        raise ValueError("The reason must not contain NUL characters.")
    return cleaned


async def main(telegram_id: int, make_admin: bool, reason: str) -> int:
    try:
        reason = clean_reason(reason)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    # Never used here; Container just needs one. The client connects lazily.
    redis_client = Redis.from_url(str(settings.redis.url))
    try:
        async with db_helper.session_factory() as session:
            # SetAdminUseCase sends no analytics events.
            container = Container(session=session, redis_client=redis_client, analytics_tracker=NoopAnalyticsTracker())
            try:
                user, changed = await container.set_admin_use_case().execute(telegram_id, make_admin, reason)
            except UserNotFoundError:
                print(
                    f"Error: no user with telegram_id {telegram_id}. The user must log in at least once first.",
                    file=sys.stderr,
                )
                return 1
            except UserBannedError:
                print(f"Error: the user with telegram_id {telegram_id} is banned and cannot become an admin.", file=sys.stderr)
                return 1
    finally:
        await redis_client.aclose()

    role = "admin" if make_admin else "not an admin"
    if changed:
        print(f"Done: user {user.id} (telegram_id {telegram_id}) is now {role}.")
    else:
        print(f"Nothing changed: user {user.id} (telegram_id {telegram_id}) already is {role}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grant or revoke the admin role.")
    parser.add_argument("--telegram-id", type=int, required=True, help="telegram_id of the user")
    parser.add_argument("--revoke", action="store_true", help="revoke the admin role instead of granting it")
    parser.add_argument("--reason", required=True, help="why; written to the admin_actions journal")
    arguments = parser.parse_args()
    sys.exit(asyncio.run(main(arguments.telegram_id, not arguments.revoke, arguments.reason)))
