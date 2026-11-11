from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status

from app.api.v1.dependencies.auth import get_current_user
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.domain.entities import UserEntity
from app.domain.enums import RateLimitAction
from app.domain.exceptions import RateLimitExceededError


def too_many_requests(exc: RateLimitExceededError) -> HTTPException:
    # `code` tells this 429 apart from `contact_limit_reached`, the daily limit of contact opens.
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "rate_limited",
            "message": str(exc),
            "action": exc.action.value,
            "retry_after_seconds": exc.retry_after_seconds,
        },
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


def rate_limited(action: RateLimitAction) -> Callable[..., Awaitable[None]]:
    """A route dependency that counts one request of the user against `action` and answers 429 over the limit.

    Declare it in the decorator's `dependencies=[...]`: those run before the parameters, so the order is the token
    (`get_current_user`, cached for the request), then this limit, then `require_username` and the handler. An invalid
    token never reaches the counter; a 409 or 422 after the limit has still spent one request."""

    async def check_rate_limit(
        current_user: UserEntity = Depends(get_current_user),
        container: Container = Depends(get_container),
    ) -> None:
        try:
            await container.rate_limit_guard().check(action, current_user.id)
        except RateLimitExceededError as exc:
            raise too_many_requests(exc) from exc

    return check_rate_limit
