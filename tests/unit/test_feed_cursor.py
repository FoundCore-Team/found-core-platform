import base64
import json

import pytest

from app.api.v1.mappers.feed import decode_feed_cursor, encode_feed_cursor, map_feed_query_to_feed_filters
from app.domain.entities import FeedCursor
from app.domain.exceptions import InvalidFeedCursorError, InvalidFeedFilterError

HASH_A = "a" * 64
HASH_B = "0123456789abcdef" * 4
MAX_OFFSET = 10_000


def b64(value: object) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def good(**overrides) -> dict:
    return {**{"o": 3, "p": HASH_A, "s": HASH_B}, **overrides}


def test_a_cursor_survives_a_round_trip():
    cursor = FeedCursor(offset=40, params_hash=HASH_A, session_key=HASH_B)

    assert decode_feed_cursor(encode_feed_cursor(cursor), MAX_OFFSET) == cursor


def test_an_encoded_cursor_is_url_safe_and_short():
    encoded = encode_feed_cursor(FeedCursor(10_000, HASH_A, HASH_B))

    assert encoded.replace("-", "").replace("_", "").isalnum()
    assert len(encoded) < 512


def test_the_largest_allowed_offset_is_accepted():
    assert decode_feed_cursor(b64(good(o=MAX_OFFSET)), MAX_OFFSET).offset == MAX_OFFSET


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "!!!",
        "not base64 at all",
        "é",
        "a" * 10_000,
        b64(b"this is not json"),
        b64([1, 2, 3]),
        b64("a string"),
        b64(None),
        b64(good(o=True)),
        b64(good(o=-1)),
        b64(good(o=MAX_OFFSET + 1)),
        b64(good(o=2**80)),
        b64(good(o=1.5)),
        b64(good(o="3")),
        b64(good(p="short")),
        b64(good(p=HASH_A.upper())),
        b64(good(p="g" * 64)),
        b64(good(p="é" * 64)),
        b64(good(p=123)),
        b64(good(s=None)),
        b64({"o": 3, "p": HASH_A}),
        b64(good(extra="field")),
        b64(b"[" * 100_000),
    ],
)
def test_a_cursor_that_is_not_exactly_what_we_issued_is_refused(raw):
    with pytest.raises(InvalidFeedCursorError):
        decode_feed_cursor(raw, MAX_OFFSET)


def test_characters_outside_the_alphabet_are_not_silently_skipped():
    valid = encode_feed_cursor(FeedCursor(1, HASH_A, HASH_B))

    with pytest.raises(InvalidFeedCursorError):
        decode_feed_cursor(valid[:10] + "!!" + valid[10:], MAX_OFFSET)


# ---------- the `attr` query parameter ----------


def filters_from(attr: list[str]):
    return map_feed_query_to_feed_filters(None, attr, [], None, False)


def test_an_attribute_pair_is_split_at_the_first_colon():
    assert filters_from(["grade:senior", "note: a:b "]).attributes == {"grade": "senior", "note": "a:b"}


def test_the_other_filters_are_passed_through():
    result = map_feed_query_to_feed_filters(5, [], [3, 1], "de", True)

    assert (result.role_id, result.tag_ids, result.country_code, result.same_timezone) == (5, [3, 1], "de", True)


@pytest.mark.parametrize(
    "attr",
    [
        ["no-colon"],
        [":value"],
        ["key:"],
        ["key:   "],
        [""],
        ["grade:senior", "grade:junior"],
        ["k" * 65 + ":v"],
        ["k:" + "v" * 129],
    ],
)
def test_a_malformed_attribute_is_a_filter_error(attr):
    with pytest.raises(InvalidFeedFilterError) as error:
        filters_from(attr)

    assert error.value.reason == "attribute_malformed"
