"""Direct DB mutation helpers for integration tests.

Bypass the repository on purpose: `SqlAlchemyUserRepository.update()` (via
`apply_user_entity_to_user_model`) intentionally never touches `is_banned`,
`ban_reason`, or `token_version`, and the admin endpoints enforce invariants
(an admin is never banned, a ban bumps `token_version`). A test that needs a
user in an exact state, whatever the invariants say, sets it directly here.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_user_banned(session: AsyncSession, user_id: int, ban_reason: str | None) -> None:
    await session.execute(
        text("UPDATE users SET is_banned = true, ban_reason = :reason WHERE id = :id"),
        {"reason": ban_reason, "id": user_id},
    )
    await session.commit()


async def set_user_admin(session: AsyncSession, user_id: int) -> None:
    await session.execute(text("UPDATE users SET is_admin = true WHERE id = :id"), {"id": user_id})
    await session.commit()


async def revoke_user_admin(session: AsyncSession, user_id: int) -> None:
    await session.execute(text("UPDATE users SET is_admin = false WHERE id = :id"), {"id": user_id})
    await session.commit()


async def set_user_deleted(session: AsyncSession, user_id: int) -> None:
    await session.execute(text("UPDATE users SET deleted_at = now() WHERE id = :id"), {"id": user_id})
    await session.commit()


async def bump_token_version(session: AsyncSession, user_id: int, new_version: int) -> None:
    await session.execute(
        text("UPDATE users SET token_version = :v WHERE id = :id"),
        {"v": new_version, "id": user_id},
    )
    await session.commit()
