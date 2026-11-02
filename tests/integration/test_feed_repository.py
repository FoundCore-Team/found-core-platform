"""SqlAlchemyFeedRepository against real Postgres with vectors of a known cosine."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.domain.entities import FeedFilters, FeedViewer
from app.domain.enums import ProfileStatus, TagStatus
from app.infrastructure.models import ProfileViewModel
from app.infrastructure.repositories import SqlAlchemyFeedRepository
from tests.fixtures.factories import make_feed_viewer
from tests.fixtures.feed_data import (
    CURRENT_MODEL,
    add_profile,
    add_user,
    add_vector,
    basis_vector,
    vector_with_cosine,
)
from tests.fixtures.taxonomy_data import TaxonomyIds, add_tag

NO_FILTERS = dict(role_id=None, attributes={}, tag_ids=[], country_code=None, same_timezone=False)


def filters(**overrides) -> FeedFilters:
    return FeedFilters(**{**NO_FILTERS, **overrides})


@pytest.fixture
def repository(session) -> SqlAlchemyFeedRepository:
    return SqlAlchemyFeedRepository(session)


@pytest.fixture
async def viewer(session, taxonomy_data: TaxonomyIds) -> FeedViewer:
    user_id = await add_user(session, telegram_id=1)
    profile_id = await add_profile(
        session, user_id, taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id
    )
    await add_vector(session, profile_id, basis_vector())
    return make_feed_viewer(
        profile_id=profile_id,
        user_id=user_id,
        category_id=taxonomy_data.tech_category_id,
        role_id=taxonomy_data.engineering_role_id,
    )


async def other_profile(session, taxonomy: TaxonomyIds, telegram_id: int, *, role="engineering", user=None, **kwargs):
    """A profile of another user in the tech category (design/engineering) unless `category` is overridden."""
    category_id = kwargs.pop("category_id", taxonomy.tech_category_id)
    role_id = {
        "engineering": taxonomy.engineering_role_id,
        "design": taxonomy.design_role_id,
        "study_mate": taxonomy.study_mate_role_id,
    }[role]
    user_id = await add_user(session, telegram_id, **(user or {}))
    return await add_profile(session, user_id, category_id, role_id, **kwargs)


def ids(candidates) -> list[int]:
    return [candidate.profile_id for candidate in candidates]


# ---------- visibility ----------


async def test_discovery_returns_exactly_the_visible_profiles_of_the_viewers_category(
    session, repository, viewer, taxonomy_data
):
    visible_a = await other_profile(session, taxonomy_data, 10)
    visible_b = await other_profile(session, taxonomy_data, 11, role="design")
    await other_profile(session, taxonomy_data, 12, role="study_mate", category_id=taxonomy_data.edu_category_id)
    await other_profile(session, taxonomy_data, 13, status=ProfileStatus.PAUSED)
    await other_profile(session, taxonomy_data, 14, status=ProfileStatus.HIDDEN_BY_ADMIN)
    await other_profile(session, taxonomy_data, 15, user={"is_banned": True})
    await other_profile(session, taxonomy_data, 16, user={"username": None})
    await other_profile(session, taxonomy_data, 17, user={"username": ""})
    await other_profile(session, taxonomy_data, 18, user={"deleted": True})

    candidates = await repository.list_discovery_candidates(viewer, CURRENT_MODEL)

    assert ids(candidates) == [visible_a, visible_b]


async def test_none_of_the_viewers_own_profiles_is_a_candidate(session, repository, viewer, taxonomy_data):
    await add_profile(session, viewer.user_id, taxonomy_data.tech_category_id, taxonomy_data.design_role_id)
    other = await other_profile(session, taxonomy_data, 10)

    assert ids(await repository.list_discovery_candidates(viewer, CURRENT_MODEL)) == [other]


async def test_candidates_come_back_ordered_by_profile_id(session, repository, viewer, taxonomy_data):
    created = [await other_profile(session, taxonomy_data, 100 + n) for n in range(5)]

    assert ids(await repository.list_discovery_candidates(viewer, CURRENT_MODEL)) == created


async def test_a_candidate_carries_its_signals(session, repository, viewer, taxonomy_data):
    tag = await add_tag(session, "Python", scopes=[(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)])
    profile_id = await other_profile(
        session,
        taxonomy_data,
        10,
        country_code="DE",
        timezone_id="Europe/Berlin",
        tag_ids=(tag.id,),
        user={"last_active_days_ago": 5},
    )

    [candidate] = await repository.list_discovery_candidates(viewer, CURRENT_MODEL)

    assert candidate.profile_id == profile_id
    assert candidate.category_id == taxonomy_data.tech_category_id
    assert candidate.role_id == taxonomy_data.engineering_role_id
    assert (candidate.country_code, candidate.timezone) == ("DE", "Europe/Berlin")
    assert candidate.tag_ids == [tag.id]
    assert candidate.matched_filters == []
    assert candidate.last_active_at.tzinfo is not None


# ---------- tags ----------


async def test_rejected_tags_are_hidden_and_pending_ones_are_shown(session, repository, viewer, taxonomy_data):
    scope = [(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)]
    approved = await add_tag(session, "Python", scopes=scope)
    pending = await add_tag(session, "Zig", status=TagStatus.PENDING, scopes=scope)
    rejected = await add_tag(session, "Spam", status=TagStatus.REJECTED, scopes=scope)
    await other_profile(session, taxonomy_data, 10, tag_ids=(rejected.id, pending.id, approved.id))

    [candidate] = await repository.list_discovery_candidates(viewer, CURRENT_MODEL)

    assert candidate.tag_ids == sorted([approved.id, pending.id])


# ---------- cosine similarity ----------


async def test_cosine_is_computed_in_the_database_for_comparable_vectors(
    session, repository, viewer, taxonomy_data
):
    close = await other_profile(session, taxonomy_data, 10)
    await add_vector(session, close, vector_with_cosine(0.6))
    orthogonal = await other_profile(session, taxonomy_data, 11)
    await add_vector(session, orthogonal, vector_with_cosine(0.0))
    opposite = await other_profile(session, taxonomy_data, 12)
    await add_vector(session, opposite, vector_with_cosine(-0.5))

    by_id = {c.profile_id: c.cos_sim for c in await repository.list_discovery_candidates(viewer, CURRENT_MODEL)}

    assert by_id[close] == pytest.approx(0.6, abs=1e-4)
    assert by_id[orthogonal] == pytest.approx(0.0, abs=1e-4)
    # A negative cosine is clamped to 0 instead of lowering the score.
    assert by_id[opposite] == 0.0


async def test_cosine_is_null_when_the_candidate_has_no_vector_or_one_of_another_model(
    session, repository, viewer, taxonomy_data
):
    no_row = await other_profile(session, taxonomy_data, 10)
    other_model = await other_profile(session, taxonomy_data, 11)
    await add_vector(session, other_model, vector_with_cosine(0.9), model="openai-large")

    by_id = {c.profile_id: c.cos_sim for c in await repository.list_discovery_candidates(viewer, CURRENT_MODEL)}

    assert by_id == {no_row: None, other_model: None}


async def test_cosine_is_null_for_everyone_when_the_viewer_has_no_comparable_vector(
    session, repository, taxonomy_data
):
    viewer_user = await add_user(session, 1)
    viewer_profile = await add_profile(
        session, viewer_user, taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id
    )
    viewer_without_vector = make_feed_viewer(
        profile_id=viewer_profile, user_id=viewer_user, category_id=taxonomy_data.tech_category_id
    )
    candidate = await other_profile(session, taxonomy_data, 10)
    await add_vector(session, candidate, vector_with_cosine(0.9))

    [row] = await repository.list_discovery_candidates(viewer_without_vector, CURRENT_MODEL)
    assert row.cos_sim is None

    await add_vector(session, viewer_profile, basis_vector(), model="openai-large")
    [row] = await repository.list_discovery_candidates(viewer_without_vector, CURRENT_MODEL)
    assert row.cos_sim is None


# ---------- search criteria ----------


async def test_a_role_filter_matches_only_that_role_and_drops_non_matching_profiles(
    session, repository, viewer, taxonomy_data
):
    engineer = await other_profile(session, taxonomy_data, 10)
    await other_profile(session, taxonomy_data, 11, role="design")

    candidates = await repository.list_search_candidates(
        viewer, filters(role_id=taxonomy_data.engineering_role_id), [], CURRENT_MODEL
    )

    assert ids(candidates) == [engineer]
    assert candidates[0].matched_filters == ["role"]


async def test_an_attribute_filter_matches_equal_values_and_not_unset_ones(session, repository, viewer, taxonomy_data):
    junior = await other_profile(session, taxonomy_data, 10, extra_attributes={"grade": "junior"})
    middle = await other_profile(session, taxonomy_data, 11, extra_attributes={"grade": "middle"})
    unset = await other_profile(session, taxonomy_data, 12, extra_attributes={})

    candidates = await repository.list_search_candidates(
        viewer,
        filters(role_id=taxonomy_data.engineering_role_id, attributes={"grade": "junior"}),
        [],
        CURRENT_MODEL,
    )

    assert {c.profile_id: c.matched_filters for c in candidates} == {
        junior: ["role", "attr:grade"],
        # Same role but a different grade, and the unset one: only the role criterion matches.
        middle: ["role"],
        unset: ["role"],
    }


async def test_a_tag_filter_matches_any_of_the_selected_tags(session, repository, viewer, taxonomy_data):
    scope = [(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)]
    go = await add_tag(session, "Go", scopes=scope)
    rust = await add_tag(session, "Rust", scopes=scope)
    cobol = await add_tag(session, "Cobol", scopes=scope)
    with_go = await other_profile(session, taxonomy_data, 10, tag_ids=(go.id,))
    with_rust = await other_profile(session, taxonomy_data, 11, tag_ids=(rust.id, cobol.id))
    await other_profile(session, taxonomy_data, 12, tag_ids=(cobol.id,))

    candidates = await repository.list_search_candidates(
        viewer, filters(tag_ids=[go.id, rust.id]), [], CURRENT_MODEL
    )

    assert {c.profile_id: c.matched_filters for c in candidates} == {with_go: ["tags"], with_rust: ["tags"]}


async def test_a_country_filter_matches_the_country_code(session, repository, viewer, taxonomy_data):
    german = await other_profile(session, taxonomy_data, 10, country_code="DE", timezone_id="Europe/Berlin")
    await other_profile(session, taxonomy_data, 11)

    candidates = await repository.list_search_candidates(viewer, filters(country_code="DE"), [], CURRENT_MODEL)

    assert {c.profile_id: c.matched_filters for c in candidates} == {german: ["country"]}


async def test_a_timezone_filter_matches_any_zone_of_the_given_list(session, repository, viewer, taxonomy_data):
    moscow = await other_profile(session, taxonomy_data, 10, timezone_id="Europe/Moscow")
    kirov = await other_profile(session, taxonomy_data, 11, timezone_id="Europe/Kirov")
    await other_profile(session, taxonomy_data, 12, country_code="DE", timezone_id="Europe/Berlin")

    candidates = await repository.list_search_candidates(
        viewer, filters(same_timezone=True), ["Europe/Moscow", "Europe/Kirov"], CURRENT_MODEL
    )

    assert {c.profile_id: c.matched_filters for c in candidates} == {moscow: ["timezone"], kirov: ["timezone"]}


async def test_matched_criteria_follow_the_order_of_the_filter_criteria(session, repository, viewer, taxonomy_data):
    scope = [(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)]
    go = await add_tag(session, "Go", scopes=scope)
    perfect = await other_profile(
        session, taxonomy_data, 10, extra_attributes={"grade": "junior"}, tag_ids=(go.id,)
    )
    wanted = filters(
        role_id=taxonomy_data.engineering_role_id,
        attributes={"grade": "junior"},
        tag_ids=[go.id],
        country_code="RU",
        same_timezone=True,
    )

    [candidate] = await repository.list_search_candidates(viewer, wanted, ["Europe/Moscow"], CURRENT_MODEL)

    assert candidate.profile_id == perfect
    assert candidate.matched_filters == wanted.criteria()


async def test_two_attributes_are_two_separate_criteria(session, repository, viewer, taxonomy_data):
    one_of_two = await other_profile(session, taxonomy_data, 10, extra_attributes={"grade": "junior", "format": "office"})

    [candidate] = await repository.list_search_candidates(
        viewer,
        filters(
            role_id=taxonomy_data.design_role_id,
            attributes={"grade": "junior", "format": "remote"},
        ),
        [],
        CURRENT_MODEL,
    )

    assert candidate.profile_id == one_of_two
    assert candidate.matched_filters == ["attr:grade"]


async def test_a_search_without_any_criterion_finds_nobody_instead_of_failing(
    session, repository, viewer, taxonomy_data
):
    await other_profile(session, taxonomy_data, 10)

    assert await repository.list_search_candidates(viewer, filters(), [], CURRENT_MODEL) == []


async def test_search_stays_inside_the_viewers_category_and_the_visibility_rules(
    session, repository, viewer, taxonomy_data
):
    await other_profile(
        session, taxonomy_data, 10, role="study_mate", category_id=taxonomy_data.edu_category_id
    )
    await other_profile(session, taxonomy_data, 11, status=ProfileStatus.PAUSED)
    await other_profile(session, taxonomy_data, 12, user={"is_banned": True})
    await other_profile(session, taxonomy_data, 13, user={"username": None})

    candidates = await repository.list_search_candidates(viewer, filters(country_code="RU"), [], CURRENT_MODEL)

    assert candidates == []


async def test_search_candidates_carry_the_cosine_too(session, repository, viewer, taxonomy_data):
    profile = await other_profile(session, taxonomy_data, 10)
    await add_vector(session, profile, vector_with_cosine(0.7))

    [candidate] = await repository.list_search_candidates(viewer, filters(country_code="RU"), [], CURRENT_MODEL)

    assert candidate.cos_sim == pytest.approx(0.7, abs=1e-4)


async def test_a_hostile_attribute_key_or_value_is_data_not_sql(session, repository, viewer, taxonomy_data):
    await other_profile(session, taxonomy_data, 10)
    hostile = "x'; DROP TABLE profiles; --"

    candidates = await repository.list_search_candidates(
        viewer,
        filters(role_id=taxonomy_data.engineering_role_id, attributes={hostile: hostile}),
        [],
        CURRENT_MODEL,
    )

    # The role criterion still matches; the hostile attribute is just a criterion nobody satisfies.
    assert [c.matched_filters for c in candidates] == [["role"]]
    assert (await session.execute(text("SELECT count(*) FROM profiles"))).scalar_one() == 2


# ---------- bio tier ----------


async def test_the_bio_tier_spans_every_category_and_applies_the_threshold(session, repository, viewer, taxonomy_data):
    same_category = await other_profile(session, taxonomy_data, 10)
    await add_vector(session, same_category, vector_with_cosine(0.5))
    other_category = await other_profile(
        session, taxonomy_data, 11, role="study_mate", category_id=taxonomy_data.edu_category_id
    )
    await add_vector(session, other_category, vector_with_cosine(0.8))
    too_far = await other_profile(session, taxonomy_data, 12)
    await add_vector(session, too_far, vector_with_cosine(0.2))
    await other_profile(session, taxonomy_data, 13)  # no vector at all

    candidates = await repository.list_bio_candidates(viewer, [], 0.35, CURRENT_MODEL)

    assert ids(candidates) == [same_category, other_category]
    assert [c.cos_sim for c in candidates] == [pytest.approx(0.5, abs=1e-4), pytest.approx(0.8, abs=1e-4)]
    assert all(c.matched_filters == [] for c in candidates)


async def test_the_bio_tier_skips_excluded_profiles(session, repository, viewer, taxonomy_data):
    kept = await other_profile(session, taxonomy_data, 10)
    excluded = await other_profile(session, taxonomy_data, 11)
    for profile in (kept, excluded):
        await add_vector(session, profile, vector_with_cosine(0.9))

    assert ids(await repository.list_bio_candidates(viewer, [excluded], 0.35, CURRENT_MODEL)) == [kept]


async def test_the_bio_tier_needs_a_viewer_vector_and_the_same_model(session, repository, taxonomy_data):
    viewer_user = await add_user(session, 1)
    viewer_profile = await add_profile(
        session, viewer_user, taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id
    )
    lonely = make_feed_viewer(profile_id=viewer_profile, user_id=viewer_user)
    candidate = await other_profile(session, taxonomy_data, 10)
    await add_vector(session, candidate, vector_with_cosine(0.9))

    assert await repository.list_bio_candidates(lonely, [], 0.0, CURRENT_MODEL) == []

    await add_vector(session, viewer_profile, basis_vector())
    assert ids(await repository.list_bio_candidates(lonely, [], 0.0, CURRENT_MODEL)) == [candidate]
    assert await repository.list_bio_candidates(lonely, [], 0.0, "another-model") == []


async def test_the_bio_tier_applies_the_visibility_rules(session, repository, viewer, taxonomy_data):
    hidden = [
        await other_profile(session, taxonomy_data, 10, status=ProfileStatus.PAUSED),
        await other_profile(session, taxonomy_data, 11, user={"is_banned": True}),
        await other_profile(session, taxonomy_data, 12, user={"username": None}),
        await other_profile(session, taxonomy_data, 13, user={"deleted": True}),
    ]
    for profile in hidden:
        await add_vector(session, profile, vector_with_cosine(0.9))
    await add_vector(
        session,
        await add_profile(session, viewer.user_id, taxonomy_data.tech_category_id, taxonomy_data.design_role_id),
        vector_with_cosine(0.9),
    )

    assert await repository.list_bio_candidates(viewer, [], 0.0, CURRENT_MODEL) == []


# ---------- single card ----------


async def test_get_candidate_returns_a_visible_profile_of_any_category(session, repository, viewer, taxonomy_data):
    profile = await other_profile(
        session, taxonomy_data, 10, role="study_mate", category_id=taxonomy_data.edu_category_id
    )
    await add_vector(session, profile, vector_with_cosine(0.4))

    candidate = await repository.get_candidate(viewer, profile, CURRENT_MODEL)

    assert candidate is not None
    assert candidate.category_id == taxonomy_data.edu_category_id
    assert candidate.cos_sim == pytest.approx(0.4, abs=1e-4)
    assert candidate.matched_filters == []


async def test_get_candidate_hides_what_the_feed_hides(session, repository, viewer, taxonomy_data):
    own = await add_profile(session, viewer.user_id, taxonomy_data.tech_category_id, taxonomy_data.design_role_id)
    paused = await other_profile(session, taxonomy_data, 10, status=ProfileStatus.PAUSED)
    banned = await other_profile(session, taxonomy_data, 11, user={"is_banned": True})
    no_username = await other_profile(session, taxonomy_data, 12, user={"username": ""})

    for profile_id in (own, paused, banned, no_username, 999_999):
        assert await repository.get_candidate(viewer, profile_id, CURRENT_MODEL) is None


# ---------- cards ----------


async def test_cards_keep_the_requested_order_and_drop_what_is_not_visible(session, repository, viewer, taxonomy_data):
    first = await other_profile(session, taxonomy_data, 10)
    second = await other_profile(session, taxonomy_data, 11, role="design")
    paused = await other_profile(session, taxonomy_data, 12, status=ProfileStatus.PAUSED)
    banned = await other_profile(session, taxonomy_data, 13, user={"is_banned": True})
    own = await add_profile(session, viewer.user_id, taxonomy_data.tech_category_id, taxonomy_data.design_role_id)

    cards = await repository.get_cards(viewer, [second, paused, 999_999, first, banned, own])

    assert [card.profile.id for card in cards] == [second, first]


async def test_cards_with_no_ids_are_empty(repository, viewer):
    assert await repository.get_cards(viewer, []) == []


async def test_a_card_carries_the_public_name_and_the_profile_content(session, repository, viewer, taxonomy_data):
    scope = [(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)]
    tag = await add_tag(session, "Python", scopes=scope)
    profile_id = await other_profile(
        session, taxonomy_data, 10, extra_attributes={"grade": "junior"}, tag_ids=(tag.id,)
    )

    [card] = await repository.get_cards(viewer, [profile_id])

    assert card.first_name == "User10"
    assert card.photo_url is None
    assert card.profile.extra_attributes == {"grade": "junior"}
    assert [t.title for t in card.profile.tags] == ["Python"]
    assert card.is_viewed is False


async def test_a_card_hides_rejected_tags_and_shows_pending_ones(session, repository, viewer, taxonomy_data):
    scope = [(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)]
    approved = await add_tag(session, "Python", scopes=scope)
    pending = await add_tag(session, "Zig", status=TagStatus.PENDING, scopes=scope)
    rejected = await add_tag(session, "Spam", status=TagStatus.REJECTED, scopes=scope)
    profile_id = await other_profile(session, taxonomy_data, 10, tag_ids=(approved.id, pending.id, rejected.id))

    [card] = await repository.get_cards(viewer, [profile_id])

    assert [t.id for t in card.profile.tags] == sorted([approved.id, pending.id])


async def test_is_viewed_follows_the_viewers_active_profile_only(session, repository, viewer, taxonomy_data):
    seen = await other_profile(session, taxonomy_data, 10)
    seen_by_someone_else = await other_profile(session, taxonomy_data, 11)
    never_seen = await other_profile(session, taxonomy_data, 12)
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            ProfileViewModel(viewer_profile_id=viewer.profile_id, viewed_profile_id=seen, viewed_at=now),
            ProfileViewModel(viewer_profile_id=never_seen, viewed_profile_id=seen_by_someone_else, viewed_at=now),
        ]
    )
    await session.commit()

    cards = await repository.get_cards(viewer, [seen, seen_by_someone_else, never_seen])

    assert [card.is_viewed for card in cards] == [True, False, False]
