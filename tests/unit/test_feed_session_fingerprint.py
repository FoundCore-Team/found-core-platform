import hashlib
from datetime import timedelta

import pytest

from app.core.config import FeedConfig
from app.domain.constants import FEED_ALGORITHM_VERSION
from app.domain.entities import FeedFilters
from app.domain.enums import FeedMode
from app.domain.services import build_feed_params_hash, build_feed_session_key
from tests.fixtures.factories import FEED_NOW

FILTERS = FeedFilters(role_id=5, attributes={"grade": "senior", "format": "remote"}, tag_ids=[3, 1, 2], country_code="RU", same_timezone=True)
SCORING = FeedConfig().scoring_fingerprint()


def params_hash(viewer_profile_id=10, mode=FeedMode.SEARCH, filters=FILTERS) -> str:
    return build_feed_params_hash(viewer_profile_id, mode, filters)


def session_key(
    hash_=None, updated_at=FEED_NOW, has_vector=True, version=FEED_ALGORITHM_VERSION, scoring=SCORING
) -> str:
    return build_feed_session_key(hash_ or params_hash(), updated_at, has_vector, version, scoring)


def test_hashes_are_64_hex_characters():
    for value in (params_hash(), session_key()):
        assert len(value) == 64
        int(value, 16)


def test_tag_and_attribute_order_do_not_change_either_hash():
    shuffled = FeedFilters(
        role_id=5,
        attributes={"format": "remote", "grade": "senior"},
        tag_ids=[2, 3, 1, 1],
        country_code="RU",
        same_timezone=True,
    )

    assert params_hash(filters=shuffled) == params_hash()
    assert session_key(params_hash(filters=shuffled)) == session_key()


@pytest.mark.parametrize(
    "changed",
    [
        dict(viewer_profile_id=11),
        dict(mode=FeedMode.DISCOVERY),
        dict(filters=FeedFilters(6, FILTERS.attributes, FILTERS.tag_ids, "RU", True)),
        dict(filters=FeedFilters(5, {"grade": "middle", "format": "remote"}, FILTERS.tag_ids, "RU", True)),
        dict(filters=FeedFilters(5, FILTERS.attributes, [1, 2], "RU", True)),
        dict(filters=FeedFilters(5, FILTERS.attributes, FILTERS.tag_ids, "DE", True)),
        dict(filters=FeedFilters(5, FILTERS.attributes, FILTERS.tag_ids, "RU", False)),
        dict(filters=FeedFilters(None, FILTERS.attributes, FILTERS.tag_ids, "RU", True)),
    ],
)
def test_anything_the_user_chose_changes_the_params_hash(changed):
    assert params_hash(**changed) != params_hash()


@pytest.mark.parametrize(
    "changed",
    [
        dict(updated_at=FEED_NOW + timedelta(seconds=1)),
        dict(has_vector=False),
        dict(version=FEED_ALGORITHM_VERSION + 1),
        dict(scoring="another-fingerprint"),
    ],
)
def test_anything_that_changes_by_itself_changes_the_session_key(changed):
    # None of these inputs reach build_feed_params_hash, so the cursor's params hash survives them.
    assert session_key(**changed) != session_key()


def test_a_different_params_hash_gives_a_different_session_key():
    assert session_key(params_hash(viewer_profile_id=11)) != session_key()


def test_hashing_is_stable_across_calls():
    assert params_hash() == params_hash()
    assert session_key() == session_key()


def test_the_canonical_encoding_does_not_drift():
    # Pins the encoding with a hand-written JSON string: changing it silently would orphan every live session and
    # invalidate every cursor already handed out.
    canonical = (
        '{"attributes":[],"country_code":null,"mode":"discovery","role_id":null,'
        '"same_timezone":false,"tag_ids":[],"viewer_profile_id":1}'
    )

    value = build_feed_params_hash(1, FeedMode.DISCOVERY, FeedFilters(None, {}, [], None, False))

    assert value == hashlib.sha256(canonical.encode("ascii")).hexdigest()
