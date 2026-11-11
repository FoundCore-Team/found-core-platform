from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.domain.entities import UserEntity
from app.domain.exceptions import (
    AdminRequiredError,
    TokenExpiredError,
    TokenInvalidError,
    UserBannedError,
    UsernameRequiredError,
)

bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    container: Container = Depends(get_container),
) -> UserEntity:
    try:
        return await container.verify_access_token_use_case().execute(credentials.credentials)
    except (TokenInvalidError, TokenExpiredError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except UserBannedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.ban_reason or str(exc)) from exc


async def require_admin(current_user: UserEntity = Depends(get_current_user)) -> UserEntity:
    # `current_user` was read from the database for this very request, so `is_admin` is live: an admin demoted since
    # the token was issued loses access at once. The token itself carries no admin claim.
    # 403 with a `code` (not a bare string like the ban's 403) so a client tells the two apart without parsing text.
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "admin_required", "message": AdminRequiredError.message},
        )
    return current_user


async def require_username(current_user: UserEntity = Depends(get_current_user)) -> UserEntity:
    # Without a Telegram username nobody can open a chat with the user, so the feed is closed for them
    # The code lets the frontend tell this apart from any other 409.
    if not current_user.username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "username_required", "message": UsernameRequiredError.message},
        )
    return current_user
