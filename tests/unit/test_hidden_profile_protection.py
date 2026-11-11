"""The owner cannot undo or get around an admin's hide: pause, resume, activate, update and delete all answer
`ProfileHiddenError`, including when the hide happens between the owner's read and the owner's write."""

import pytest

from app.domain.enums import ProfileStatus
from app.domain.exceptions import ProfileHiddenError
from tests.unit.test_profile_use_cases import ENGINEERING, Env


def hide_before_the_status_write(env: Env) -> None:
    """An admin hides the profile after the owner's use case has read it and before its status write."""
    real_set_status = env.profiles.set_status

    async def set_status(profile_id, status):
        await env.profiles.hide(profile_id)
        return await real_set_status(profile_id, status)

    env.profiles.set_status = set_status


@pytest.fixture
def env() -> Env:
    return Env()


async def test_a_pause_that_loses_to_a_hide_answers_hidden_and_keeps_the_hide(env):
    profile = await env.create()
    hide_before_the_status_write(env)

    with pytest.raises(ProfileHiddenError):
        await env.pause_use_case().execute(await env.user(), profile.id)

    assert (await env.profiles.get_by_id(profile.id)).status is ProfileStatus.HIDDEN_BY_ADMIN
    assert env.uow.commits == 0


async def test_a_resume_that_loses_to_a_hide_answers_hidden_and_keeps_the_hide(env):
    profile = await env.create()
    await env.pause_use_case().execute(await env.user(), profile.id)
    env.uow.commits = 0
    hide_before_the_status_write(env)

    with pytest.raises(ProfileHiddenError):
        await env.resume_use_case().execute(await env.user(), profile.id)

    assert (await env.profiles.get_by_id(profile.id)).status is ProfileStatus.HIDDEN_BY_ADMIN
    assert env.uow.commits == 0


async def test_a_hidden_profile_cannot_be_deleted_and_stays(env):
    profile = await env.create()
    await env.profiles.hide(profile.id)

    with pytest.raises(ProfileHiddenError):
        await env.delete_use_case().execute(await env.user(), profile.id)

    assert await env.profiles.get_by_id(profile.id) is not None
    assert env.uow.commits == 0


async def test_a_hidden_profile_cannot_be_activated(env):
    profile = await env.create()
    await env.profiles.hide(profile.id)

    with pytest.raises(ProfileHiddenError):
        await env.activate_use_case().execute(await env.user(), profile.id)


async def test_the_repository_never_overwrites_a_hidden_status_through_set_status(env):
    profile = await env.create()
    await env.profiles.hide(profile.id)

    changed = await env.profiles.set_status(profile.id, ProfileStatus.ACTIVE)

    assert changed is False
    assert (await env.profiles.get_by_id(profile.id)).status is ProfileStatus.HIDDEN_BY_ADMIN


async def test_hide_and_unhide_follow_the_documented_transitions(env):
    profile = await env.create(role=ENGINEERING)

    assert await env.profiles.unhide(profile.id) is False  # not hidden yet
    assert await env.profiles.hide(profile.id) is True
    assert await env.profiles.hide(profile.id) is False  # already hidden
    assert await env.profiles.unhide(profile.id) is True
    assert (await env.profiles.get_by_id(profile.id)).status is ProfileStatus.PAUSED  # never straight to active
