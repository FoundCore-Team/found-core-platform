"""Checks the handwritten seed fixture against the taxonomy seed, without a database.

A profile the validator would reject stops the seed script half way, so these tests are what keeps a typo in the
fixture from surfacing only when somebody runs the seed.
"""

from collections import Counter

import pytest

from app.application.services.timezone_catalog import TimezoneCatalog
from app.core.config import ProfileConfig, settings
from app.domain.services import TagTitleValidator, normalize_tag_title
from scripts.dev_seed_profiles import USERNAME_PREFIX, load_fixture
from scripts.dev_seed_taxonomy import CATEGORIES, ROLE_FIELDS, ROLES

USERS = load_fixture()
PROFILES = [(user, profile) for user in USERS for profile in user["profiles"]]
ROLE_CATEGORY = {role["slug"]: role["category_slug"] for role in ROLES}
FIELDS_BY_ROLE: dict[str, list[dict]] = {}
for _field in ROLE_FIELDS:
    FIELDS_BY_ROLE.setdefault(_field["role_slug"], []).append(_field)


def where(user: dict, profile: dict) -> str:
    return f"user {user['key']} {profile['category']}/{profile['role']}"


def test_fixture_has_about_a_hundred_profiles_of_about_ninety_users():
    assert 90 <= len(PROFILES) <= 110
    assert 80 <= len(USERS) <= 100


def test_user_keys_and_usernames_are_unique_and_marked_as_seed_data():
    assert len({user["key"] for user in USERS}) == len(USERS)
    usernames = [user["username"] for user in USERS if user["username"] is not None]
    assert len(set(usernames)) == len(usernames)
    assert all(name.startswith(USERNAME_PREFIX) for name in usernames)


def test_the_manual_scenario_user_exists():
    anna = next(user for user in USERS if user["key"] == 1)

    assert anna["username"] == "seed_anna_backend"
    assert anna["first_name"] == "Анна"
    assert anna["profiles"][0]["role"] == "engineering"


@pytest.mark.parametrize(("user", "profile"), PROFILES, ids=lambda value: value["key"] if "key" in value else value["role"])
def test_every_profile_fits_the_profile_rules(user, profile):
    config = ProfileConfig()
    label = where(user, profile)

    assert config.bio_min_length <= len(profile["bio"].strip()) <= config.bio_max_length, f"{label}: bio"
    assert config.goals_min_length <= len(profile["goals"].strip()) <= config.goals_max_length, f"{label}: goals"

    titles = [*profile["tags"], *profile.get("pending_tags", [])]
    assert config.tags_min_count <= len(titles) <= config.tags_max_count, f"{label}: tag count"
    normalized = [normalize_tag_title(title) for title in titles]
    assert len(set(normalized)) == len(normalized), f"{label}: duplicate tags"

    assert ROLE_CATEGORY.get(profile["role"]) == profile["category"], f"{label}: role is not in this category"
    assert profile["timezone"] in TimezoneCatalog().zone_ids_for(profile["country_code"]), f"{label}: timezone"
    assert profile.get("status", "active") in {"active", "paused"}, f"{label}: status"


@pytest.mark.parametrize(("user", "profile"), PROFILES, ids=lambda value: value["key"] if "key" in value else value["role"])
def test_every_profile_has_valid_role_attributes(user, profile):
    fields = {field["key"]: field for field in FIELDS_BY_ROLE[profile["role"]]}
    attributes = profile["extra_attributes"]
    label = where(user, profile)

    assert set(attributes) <= set(fields), f"{label}: unknown attribute"
    for key, field in fields.items():
        if field["is_required"]:
            assert key in attributes, f"{label}: missing required {key}"
    for key, value in attributes.items():
        assert value in {option["value"] for option in fields[key]["options"]}, f"{label}: {key}={value}"


def test_every_tag_title_would_be_accepted_from_a_user():
    validator = TagTitleValidator(
        min_length=settings.taxonomy.tag_title_min_length,
        max_length=settings.taxonomy.tag_title_max_length,
        pattern=settings.taxonomy.tag_title_allowed_pattern,
    )

    for user, profile in PROFILES:
        for title in [*profile["tags"], *profile.get("pending_tags", [])]:
            validator.validate(title)


def test_a_user_never_has_two_profiles_for_the_same_role():
    for user in USERS:
        roles = [(p["category"], p["role"]) for p in user["profiles"]]
        assert len(set(roles)) == len(roles), user["key"]


def test_every_role_and_both_categories_are_covered():
    assert {profile["role"] for _, profile in PROFILES} == {role["slug"] for role in ROLES}
    assert {profile["category"] for _, profile in PROFILES} == {category["slug"] for category in CATEGORIES}


def test_edge_cases_the_feed_needs_are_present():
    multi = [user for user in USERS if len(user["profiles"]) > 1]
    same_category = [u for u in multi if len({p["category"] for p in u["profiles"]}) == 1]
    cross_category = [u for u in multi if len({p["category"] for p in u["profiles"]}) > 1]

    assert len(multi) >= 10
    assert same_category and cross_category
    assert sum(p.get("status") == "paused" for _, p in PROFILES) >= 5
    assert sum(user["username"] is None for user in USERS) >= 3
    assert sum(user.get("is_banned", False) for user in USERS) >= 2
    assert sum(p.get("skip_embedding", False) for _, p in PROFILES) >= 2
    assert sum(bool(p.get("pending_tags")) for _, p in PROFILES) >= 2


def test_zones_with_one_offset_but_different_names_are_present():
    zones = {profile["timezone"] for _, profile in PROFILES}

    assert {"Europe/Moscow", "Europe/Kirov", "Europe/Istanbul"} <= zones


def test_activity_dates_cover_all_three_buckets_with_a_majority_of_recent_users():
    days = [user["last_active_days_ago"] for user in USERS]
    buckets = Counter("recent" if d <= 7 else "stale" if d <= 30 else "old" for d in days)

    assert buckets["recent"] > buckets["stale"] > 0
    assert buckets["old"] > 0
    assert buckets["recent"] >= len(days) * 0.5
