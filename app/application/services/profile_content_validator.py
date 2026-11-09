from app.domain.entities import ProfileFormEntity
from app.domain.enums import TagStatus
from app.domain.exceptions import ProfileTagInvalidError, TagRejectedError
from app.domain.interfaces import ITaxonomyRepository
from app.domain.services import ExtraAttributesValidator, ProfileFormValidator


class ProfileContentValidator:
    """Checks everything a client can put into a profile, shared by create and update.

    The form and the role attributes are pure rules (domain services); the tags need
    the taxonomy repository, which is why this lives in the application layer.
    """

    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        form_validator: ProfileFormValidator,
        extra_attributes_validator: ExtraAttributesValidator,
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._form_validator = form_validator
        self._extra_attributes_validator = extra_attributes_validator

    async def validate(
        self,
        category_id: int,
        role_id: int,
        form: ProfileFormEntity,
        extra_attributes: dict[str, str],
    ) -> tuple[ProfileFormEntity, dict[str, str]]:
        """Returns the cleaned form and attributes, or raises a domain error."""
        form = self._form_validator.validate(form)

        role_fields = await self._taxonomy_repository.get_role_fields_by_role(role_id)
        extra_attributes = self._extra_attributes_validator.validate(extra_attributes, role_fields)

        # The form validator already rejected duplicates and too many ids, so the
        # count below can only differ when an id does not exist or is out of scope.
        tags = await self._taxonomy_repository.get_tags_in_scope_by_ids(form.tag_ids, category_id, role_id)
        if len(tags) != len(form.tag_ids):
            raise ProfileTagInvalidError()
        if any(tag.status == TagStatus.REJECTED for tag in tags):
            raise TagRejectedError()
        # An alias id from an outdated client: it must pick the target tag again. Profiles never keep an alias.
        if any(tag.status == TagStatus.MERGED for tag in tags):
            raise ProfileTagInvalidError()

        return form, extra_attributes
