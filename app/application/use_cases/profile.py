from datetime import datetime, timezone

from app.application.services.profile_content_validator import ProfileContentValidator
from app.application.services.timezone_catalog import TimezoneCatalog
from app.core.config import ProfileConfig
from app.domain.entities import (
    CountryEntity,
    NewProfileEntity,
    ProfileEntity,
    ProfileFormEntity,
    TimezoneEntity,
    UserEntity,
)
from app.domain.enums import ProfileStatus
from app.domain.exceptions import (
    CategoryNotFoundError,
    ProfileAlreadyExistsError,
    ProfileHiddenError,
    ProfileNotActivatableError,
    ProfileNotFoundError,
    RoleNotFoundError,
)
from app.domain.interfaces import (
    IEmbeddingJobQueue,
    IProfileEmbeddingRepository,
    IProfileRepository,
    ITaxonomyRepository,
    IUnitOfWork,
    IUserRepository,
)


class GetProfileFormConfigUseCase:
    def __init__(self, profile_config: ProfileConfig, timezone_catalog: TimezoneCatalog) -> None:
        self._profile_config = profile_config
        self._timezone_catalog = timezone_catalog

    async def execute(self) -> tuple[ProfileConfig, list[CountryEntity]]:
        allowed = self._profile_config.allowed_country_codes
        countries = []
        for code, name in self._timezone_catalog.countries():
            if allowed and code not in allowed:
                continue
            timezones = [
                TimezoneEntity(
                    id=zone_id,
                    label=self._timezone_catalog.label(zone_id),
                    utc_offset=self._timezone_catalog.utc_offset(zone_id),
                )
                for zone_id in self._timezone_catalog.zone_ids_for(code)
            ]
            countries.append(CountryEntity(code=code, name=name, timezones=timezones))
        return self._profile_config, countries


class GetMyProfilesUseCase:
    def __init__(self, profile_repository: IProfileRepository) -> None:
        self._profile_repository = profile_repository

    async def execute(self, user_id: int) -> list[ProfileEntity]:
        return await self._profile_repository.list_by_user_id(user_id)


class GetProfileUseCase:
    def __init__(self, profile_repository: IProfileRepository) -> None:
        self._profile_repository = profile_repository

    async def execute(self, user_id: int, profile_id: int) -> ProfileEntity:
        profile = await self._profile_repository.get_by_id(profile_id)
        # Someone else's profile looks exactly like a missing one.
        if profile is None or profile.user_id != user_id:
            raise ProfileNotFoundError()
        return profile


class CreateProfileUseCase:
    def __init__(
        self,
        profile_repository: IProfileRepository,
        user_repository: IUserRepository,
        taxonomy_repository: ITaxonomyRepository,
        profile_content_validator: ProfileContentValidator,
        profile_embedding_repository: IProfileEmbeddingRepository,
        embedding_queue: IEmbeddingJobQueue,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._user_repository = user_repository
        self._taxonomy_repository = taxonomy_repository
        self._profile_content_validator = profile_content_validator
        self._profile_embedding_repository = profile_embedding_repository
        self._embedding_queue = embedding_queue
        self._unit_of_work = unit_of_work

    async def execute(
        self,
        user: UserEntity,
        category_id: int,
        role_id: int,
        form: ProfileFormEntity,
        extra_attributes: dict[str, str],
    ) -> ProfileEntity:
        category = await self._taxonomy_repository.get_category_by_id(category_id)
        if category is None:
            raise CategoryNotFoundError()
        role = await self._taxonomy_repository.get_role_by_id(role_id)
        if role is None or role.category_id != category_id:
            raise RoleNotFoundError()

        form, extra_attributes = await self._profile_content_validator.validate(
            category_id, role_id, form, extra_attributes
        )

        now = datetime.now(timezone.utc)
        profile = await self._profile_repository.create(
            NewProfileEntity(
                user_id=user.id,
                category_id=category_id,
                role_id=role_id,
                country_code=form.country_code,
                timezone=form.timezone,
                bio=form.bio,
                goals_description=form.goals_description,
                extra_attributes=extra_attributes,
                tag_ids=form.tag_ids,
                status=ProfileStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
        )
        if profile is None:
            # A parallel request (or a double submit) created the same user/category/role profile first.
            existing = await self._profile_repository.get_by_user_category_role(user.id, category_id, role_id)
            assert existing is not None
            raise ProfileAlreadyExistsError(existing)

        # Counted once at creation and never decreased, even if the profile is deleted later.
        await self._taxonomy_repository.increment_tags_usage(form.tag_ids)
        # Re-read so the response carries the updated usage_count of the tags.
        profile = await self._profile_repository.get_by_id(profile.id)
        assert profile is not None

        if user.active_profile_id is None:
            # The caller's copy of the user is kept in sync so the response can report is_active.
            user.active_profile_id = profile.id
            await self._user_repository.set_active_profile(user.id, profile.id)

        await self._profile_embedding_repository.mark_pending(profile.id, profile.updated_at)

        await self._unit_of_work.commit()
        # After the commit, so the worker finds the profile. Never raises: a missed job is picked up by the sweep.
        await self._embedding_queue.enqueue(profile.id)
        return profile


class UpdateProfileUseCase:
    def __init__(
        self,
        profile_repository: IProfileRepository,
        profile_content_validator: ProfileContentValidator,
        profile_embedding_repository: IProfileEmbeddingRepository,
        embedding_queue: IEmbeddingJobQueue,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._profile_content_validator = profile_content_validator
        self._profile_embedding_repository = profile_embedding_repository
        self._embedding_queue = embedding_queue
        self._unit_of_work = unit_of_work

    async def execute(
        self,
        user_id: int,
        profile_id: int,
        form: ProfileFormEntity,
        extra_attributes: dict[str, str],
    ) -> ProfileEntity:
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None or profile.user_id != user_id:
            raise ProfileNotFoundError()
        if profile.status == ProfileStatus.HIDDEN_BY_ADMIN:
            raise ProfileHiddenError()

        form, extra_attributes = await self._profile_content_validator.validate(
            profile.category_id, profile.role_id, form, extra_attributes
        )

        profile.country_code = form.country_code
        profile.timezone = form.timezone
        profile.bio = form.bio
        profile.goals_description = form.goals_description
        profile.extra_attributes = extra_attributes
        profile.updated_at = datetime.now(timezone.utc)
        updated = await self._profile_repository.update(profile, form.tag_ids)
        # The new `updated_at` makes a worker that is still embedding the old text lose its conditional write.
        await self._profile_embedding_repository.mark_pending(updated.id, updated.updated_at)

        await self._unit_of_work.commit()
        await self._embedding_queue.enqueue(updated.id)
        return updated


class ActivateProfileUseCase:
    def __init__(
        self,
        profile_repository: IProfileRepository,
        user_repository: IUserRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._user_repository = user_repository
        self._unit_of_work = unit_of_work

    async def execute(self, user: UserEntity, profile_id: int) -> ProfileEntity:
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None or profile.user_id != user.id:
            raise ProfileNotFoundError()
        if profile.status == ProfileStatus.HIDDEN_BY_ADMIN:
            raise ProfileHiddenError()
        if profile.status != ProfileStatus.ACTIVE:
            raise ProfileNotActivatableError()

        if user.active_profile_id != profile.id:
            user.active_profile_id = profile.id
            await self._user_repository.set_active_profile(user.id, profile.id)
            await self._unit_of_work.commit()
        return profile


class PauseProfileUseCase:
    def __init__(
        self,
        profile_repository: IProfileRepository,
        user_repository: IUserRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._user_repository = user_repository
        self._unit_of_work = unit_of_work

    async def execute(self, user: UserEntity, profile_id: int) -> ProfileEntity:
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None or profile.user_id != user.id:
            raise ProfileNotFoundError()
        if profile.status == ProfileStatus.HIDDEN_BY_ADMIN:
            raise ProfileHiddenError()
        if profile.status == ProfileStatus.PAUSED:
            return profile

        if not await self._profile_repository.set_status(profile.id, ProfileStatus.PAUSED):
            # An admin hid the profile after we read it: the UPDATE refused to overwrite `hidden_by_admin`.
            raise ProfileHiddenError()
        profile.status = ProfileStatus.PAUSED

        # The paused profile is no longer eligible, so the pointer moves to the newest
        # profile that still is, or becomes NULL when there is none.
        if user.active_profile_id == profile.id:
            newest_active = await self._profile_repository.get_newest_active_by_user_id(user.id)
            user.active_profile_id = newest_active.id if newest_active else None
            await self._user_repository.set_active_profile(user.id, user.active_profile_id)

        await self._unit_of_work.commit()
        return profile


class ResumeProfileUseCase:
    def __init__(
        self,
        profile_repository: IProfileRepository,
        user_repository: IUserRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._user_repository = user_repository
        self._unit_of_work = unit_of_work

    async def execute(self, user: UserEntity, profile_id: int) -> ProfileEntity:
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None or profile.user_id != user.id:
            raise ProfileNotFoundError()
        if profile.status == ProfileStatus.HIDDEN_BY_ADMIN:
            raise ProfileHiddenError()
        if profile.status == ProfileStatus.ACTIVE:
            return profile

        if not await self._profile_repository.set_status(profile.id, ProfileStatus.ACTIVE):
            raise ProfileHiddenError()
        profile.status = ProfileStatus.ACTIVE

        if user.active_profile_id is None:
            # The caller's copy of the user is kept in sync so the response can report is_active.
            user.active_profile_id = profile.id
            await self._user_repository.set_active_profile(user.id, profile.id)

        await self._unit_of_work.commit()
        return profile


class DeleteProfileUseCase:
    def __init__(
        self,
        profile_repository: IProfileRepository,
        user_repository: IUserRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._user_repository = user_repository
        self._unit_of_work = unit_of_work

    async def execute(self, user: UserEntity, profile_id: int) -> None:
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None or profile.user_id != user.id:
            raise ProfileNotFoundError()
        # Otherwise the owner could delete a hidden profile and create a fresh one, getting around the moderation.
        if profile.status == ProfileStatus.HIDDEN_BY_ADMIN:
            raise ProfileHiddenError()

        await self._profile_repository.delete(profile.id)

        # Same pointer rule as pause: newest remaining active profile, or NULL.
        if user.active_profile_id == profile.id:
            newest_active = await self._profile_repository.get_newest_active_by_user_id(user.id)
            user.active_profile_id = newest_active.id if newest_active else None
            await self._user_repository.set_active_profile(user.id, user.active_profile_id)

        await self._unit_of_work.commit()
