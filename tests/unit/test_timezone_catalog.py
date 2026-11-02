from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic_settings import BaseSettings

from app.application.services.timezone_catalog import TimezoneCatalog
from app.core.config import ProfileConfig

WINTER = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SUMMER = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def catalog() -> TimezoneCatalog:
    return TimezoneCatalog()


def test_every_listed_country_has_names_and_loadable_zones(catalog):
    countries = catalog.countries()

    assert len(countries) > 150
    for code, name in countries:
        assert len(code) == 2 and code.isupper()
        assert name
        zone_ids = catalog.zone_ids_for(code)
        assert zone_ids, code
        for zone_id in zone_ids:
            ZoneInfo(zone_id)


def test_countries_are_sorted_by_name_then_code(catalog):
    countries = catalog.countries()

    assert countries == sorted(countries, key=lambda item: (item[1], item[0]))
    assert len({code for code, _ in countries}) == len(countries)


def test_russia_has_moscow_but_not_tokyo(catalog):
    zone_ids = catalog.zone_ids_for("RU")

    assert "Europe/Moscow" in zone_ids
    assert "Asia/Tokyo" not in zone_ids
    assert len(zone_ids) > 10


def test_single_zone_country_lists_exactly_that_zone(catalog):
    assert catalog.zone_ids_for("JP") == ["Asia/Tokyo"]


def test_germany_includes_berlin(catalog):
    # zone.tab also lists the Busingen exclave, so Germany has more than one zone.
    assert "Europe/Berlin" in catalog.zone_ids_for("DE")


def test_unknown_country_has_no_zones_and_is_not_listed(catalog):
    assert catalog.zone_ids_for("XX") == []
    assert catalog.zone_ids_for("") == []
    assert not catalog.has_country("XX")
    assert not catalog.has_country("de")
    assert catalog.has_country("DE")


def test_zones_are_ordered_by_offset_then_id(catalog):
    zone_ids = catalog.zone_ids_for("RU", at=WINTER)

    keys = [(WINTER.astimezone(ZoneInfo(zone_id)).utcoffset(), zone_id) for zone_id in zone_ids]
    assert keys == sorted(keys)


@pytest.mark.parametrize(
    ("zone_id", "moment", "expected"),
    [
        ("Europe/Berlin", WINTER, "+01:00"),
        ("Europe/Berlin", SUMMER, "+02:00"),
        ("Asia/Kolkata", WINTER, "+05:30"),
        ("America/St_Johns", WINTER, "-03:30"),
        ("UTC", WINTER, "+00:00"),
    ],
)
def test_utc_offset_is_computed_for_the_given_moment(catalog, zone_id, moment, expected):
    assert catalog.utc_offset(zone_id, moment) == expected


@pytest.mark.parametrize(
    ("zone_id", "expected"),
    [
        ("Europe/Berlin", "Berlin"),
        ("America/New_York", "New York"),
        ("America/Argentina/Buenos_Aires", "Buenos Aires"),
    ],
)
def test_label_is_the_city_from_the_iana_id(catalog, zone_id, expected):
    assert catalog.label(zone_id) == expected


def test_profile_config_defaults_and_plain_model():
    config = ProfileConfig()

    assert (config.bio_min_length, config.bio_max_length) == (200, 2000)
    assert (config.goals_min_length, config.goals_max_length) == (200, 2000)
    assert (config.tags_min_count, config.tags_max_count) == (5, 15)
    assert config.allowed_country_codes == []
    # A BaseSettings default instance would read unprefixed environment variables.
    assert not issubclass(ProfileConfig, BaseSettings)


def test_moscow_kirov_and_istanbul_share_an_offset(catalog):
    zone_ids = catalog.zone_ids_for("RU")  # sanity: Kirov is a Russian zone of its own
    assert "Europe/Kirov" in zone_ids

    for moment in (WINTER, SUMMER):
        same = catalog.zone_ids_with_same_offset("Europe/Moscow", at=moment)

        assert {"Europe/Moscow", "Europe/Kirov", "Europe/Istanbul", "Europe/Minsk"} <= set(same)
        assert "Europe/Berlin" not in same


def test_same_offset_zones_are_sorted_and_include_the_zone_itself(catalog):
    same = catalog.zone_ids_with_same_offset("Europe/Berlin", at=WINTER)

    assert same == sorted(same)
    assert "Europe/Berlin" in same


def test_same_offset_follows_daylight_saving_time(catalog):
    # Winter: Berlin and Lagos are both +01:00. Summer: Berlin moves to +02:00, Lagos stays, Paris follows Berlin.
    assert "Africa/Lagos" in catalog.zone_ids_with_same_offset("Europe/Berlin", at=WINTER)
    assert "Africa/Lagos" not in catalog.zone_ids_with_same_offset("Europe/Berlin", at=SUMMER)
    for moment in (WINTER, SUMMER):
        assert "Europe/Paris" in catalog.zone_ids_with_same_offset("Europe/Berlin", at=moment)


def test_same_offset_matches_half_hour_zones_exactly(catalog):
    same = catalog.zone_ids_with_same_offset("Asia/Kolkata", at=WINTER)

    assert "Asia/Kolkata" in same
    assert "Asia/Dhaka" not in same  # +06:00


def test_a_zone_missing_from_the_catalog_still_matches_itself(catalog):
    # "UTC" is a valid ZoneInfo key but is not listed in zone.tab.
    same = catalog.zone_ids_with_same_offset("UTC", at=WINTER)

    assert "UTC" in same
    assert "Africa/Abidjan" in same
