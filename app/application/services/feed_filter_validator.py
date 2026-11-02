from dataclasses import replace

from app.application.services.timezone_catalog import TimezoneCatalog
from app.domain.entities import FeedFilters
from app.domain.enums import RoleFieldType, TagStatus
from app.domain.exceptions import InvalidFeedFilterError
from app.domain.interfaces import ITaxonomyRepository


class FeedFilterValidator:
    """Checks the Search filters against the taxonomy and returns a cleaned copy.

    Lives in the application layer because it reads the taxonomy and the timezone catalog. Search stays inside the
    viewer's category, so a role from another category is as invalid as an unknown one.
    """

    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        timezone_catalog: TimezoneCatalog,
        allowed_country_codes: list[str],
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._timezone_catalog = timezone_catalog
        self._allowed_country_codes = allowed_country_codes

    async def _validate_attributes(self, role_id: int, attributes: dict[str, str]) -> None:
        fields = {field.key: field for field in await self._taxonomy_repository.get_role_fields_by_role(role_id)}
        for key, value in attributes.items():
            field = fields.get(key)
            # Only select fields are filterable so far; any other type fails closed.
            if field is None or not field.is_filterable or field.field_type is not RoleFieldType.SELECT:
                raise InvalidFeedFilterError("attribute_not_filterable")
            if value not in {option["value"] for option in field.options or []}:
                raise InvalidFeedFilterError("attribute_value_invalid")

    def _clean_country_code(self, country_code: str) -> str:
        cleaned = country_code.strip().upper()
        if not self._timezone_catalog.has_country(cleaned):
            raise InvalidFeedFilterError("country_unknown")
        if self._allowed_country_codes and cleaned not in self._allowed_country_codes:
            raise InvalidFeedFilterError("country_unknown")
        return cleaned

    async def validate(self, viewer_category_id: int, filters: FeedFilters) -> FeedFilters:
        if filters.attributes and filters.role_id is None:
            raise InvalidFeedFilterError("attributes_require_role")

        if filters.role_id is not None:
            role = await self._taxonomy_repository.get_role_by_id(filters.role_id)
            if role is None or role.category_id != viewer_category_id:
                raise InvalidFeedFilterError("role_not_in_category")
            await self._validate_attributes(role.id, filters.attributes)

        country_code = filters.country_code
        if country_code is not None:
            country_code = self._clean_country_code(country_code)

        tag_ids = sorted(set(filters.tag_ids))
        if tag_ids:
            tags = await self._taxonomy_repository.get_tags_by_ids(tag_ids)
            # Pending and rejected tags are not part of the shared reference list; an unknown id looks the same.
            if len(tags) != len(tag_ids) or any(tag.status is not TagStatus.APPROVED for tag in tags):
                raise InvalidFeedFilterError("tag_invalid")

        return replace(filters, country_code=country_code, tag_ids=tag_ids)
