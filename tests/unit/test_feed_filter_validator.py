import pytest

from app.application.services.feed_filter_validator import FeedFilterValidator
from app.application.services.timezone_catalog import TimezoneCatalog
from app.domain.entities import FeedFilters, RoleEntity, RoleFieldEntity, TagEntity
from app.domain.enums import RoleFieldType, TagStatus
from app.domain.exceptions import InvalidFeedFilterError
from tests.fixtures.fake_taxonomy import FakeTaxonomyRepository

VIEWER_CATEGORY = 1
ENGINEERING = 5
OTHER_CATEGORY_ROLE = 9
APPROVED_TAG, SECOND_APPROVED_TAG, PENDING_TAG, REJECTED_TAG = 42, 43, 44, 45


def _field(field_id, key, *, filterable=True, field_type=RoleFieldType.SELECT, options=("junior", "senior")):
    return RoleFieldEntity(
        id=field_id,
        role_id=ENGINEERING,
        key=key,
        label=key,
        field_type=field_type,
        options=[{"value": value, "label": value} for value in options] if options else None,
        is_required=False,
        is_filterable=filterable,
    )


def _tag(tag_id, status):
    return TagEntity(
        id=tag_id, title=f"tag{tag_id}", normalized_title=f"tag{tag_id}", status=status, created_by_user_id=None
    )


def make_validator(allowed_country_codes=None) -> FeedFilterValidator:
    taxonomy = FakeTaxonomyRepository(
        roles=[
            RoleEntity(id=ENGINEERING, category_id=VIEWER_CATEGORY, slug="engineering", title="Engineering"),
            RoleEntity(id=OTHER_CATEGORY_ROLE, category_id=2, slug="study_mate", title="Study mate"),
        ],
        role_fields=[
            _field(1, "grade"),
            _field(2, "format", filterable=False, options=("remote", "office")),
            _field(3, "notes", field_type=RoleFieldType.TEXT, options=None),
        ],
        tags=[
            _tag(APPROVED_TAG, TagStatus.APPROVED),
            _tag(SECOND_APPROVED_TAG, TagStatus.APPROVED),
            _tag(PENDING_TAG, TagStatus.PENDING),
            _tag(REJECTED_TAG, TagStatus.REJECTED),
        ],
    )
    return FeedFilterValidator(taxonomy, TimezoneCatalog(), allowed_country_codes or [])


def filters(**overrides) -> FeedFilters:
    values = dict(role_id=None, attributes={}, tag_ids=[], country_code=None, same_timezone=False)
    values.update(overrides)
    return FeedFilters(**values)


async def reason_of(validator, **overrides) -> str:
    with pytest.raises(InvalidFeedFilterError) as error:
        await validator.validate(VIEWER_CATEGORY, filters(**overrides))
    return error.value.reason


async def test_a_valid_filter_is_returned_cleaned():
    result = await make_validator().validate(
        VIEWER_CATEGORY,
        filters(
            role_id=ENGINEERING,
            attributes={"grade": "senior"},
            tag_ids=[SECOND_APPROVED_TAG, APPROVED_TAG, APPROVED_TAG],
            country_code=" de ",
            same_timezone=True,
        ),
    )

    assert result == filters(
        role_id=ENGINEERING,
        attributes={"grade": "senior"},
        tag_ids=[APPROVED_TAG, SECOND_APPROVED_TAG],
        country_code="DE",
        same_timezone=True,
    )


async def test_the_timezone_flag_alone_is_a_valid_filter():
    assert await make_validator().validate(VIEWER_CATEGORY, filters(same_timezone=True)) == filters(same_timezone=True)


async def test_a_role_from_another_category_is_refused():
    assert await reason_of(make_validator(), role_id=OTHER_CATEGORY_ROLE) == "role_not_in_category"


async def test_an_unknown_role_is_refused():
    assert await reason_of(make_validator(), role_id=999) == "role_not_in_category"


async def test_attributes_without_a_role_are_refused():
    assert await reason_of(make_validator(), attributes={"grade": "senior"}) == "attributes_require_role"


@pytest.mark.parametrize("attributes", [{"unknown": "x"}, {"format": "remote"}, {"notes": "anything"}])
async def test_only_filterable_select_fields_of_the_role_are_accepted(attributes):
    assert await reason_of(make_validator(), role_id=ENGINEERING, attributes=attributes) == "attribute_not_filterable"


@pytest.mark.parametrize("value", ["lead", "", "SENIOR"])
async def test_an_attribute_value_must_be_one_of_the_options(value):
    reason = await reason_of(make_validator(), role_id=ENGINEERING, attributes={"grade": value})

    assert reason == "attribute_value_invalid"


@pytest.mark.parametrize("country_code", ["XX", "", "  ", "Germany", "d"])
async def test_an_unknown_country_is_refused(country_code):
    assert await reason_of(make_validator(), country_code=country_code) == "country_unknown"


async def test_a_country_outside_the_allowed_list_is_refused():
    validator = make_validator(allowed_country_codes=["DE"])

    assert await reason_of(validator, country_code="FR") == "country_unknown"
    assert (await validator.validate(VIEWER_CATEGORY, filters(country_code="de"))).country_code == "DE"


@pytest.mark.parametrize("tag_ids", [[PENDING_TAG], [REJECTED_TAG], [999], [APPROVED_TAG, PENDING_TAG], [APPROVED_TAG, 999]])
async def test_only_approved_existing_tags_are_accepted(tag_ids):
    assert await reason_of(make_validator(), tag_ids=tag_ids) == "tag_invalid"
