from app.core.config import FeedConfig
from app.domain.entities import FeedFilters
from app.domain.enums import FeedReasonCode, FeedTier
from app.domain.services import FeedRanker
from tests.fixtures.factories import FEED_NOW, make_feed_candidate, make_feed_viewer
from tests.unit.test_feed_scorer import make_scorer

THREE_CRITERIA = FeedFilters(role_id=5, attributes={"grade": "senior"}, tag_ids=[42], country_code=None, same_timezone=False)
ALL = ["role", "attr:grade", "tags"]


def make_ranker(session_cap: int = 200) -> FeedRanker:
    return FeedRanker(make_scorer(FeedConfig()), session_cap)


def search(candidates, filters=THREE_CRITERIA, zones=(), session_cap=200):
    return make_ranker(session_cap).rank_search(make_feed_viewer(), filters, candidates, FEED_NOW, zones)


def ids(items) -> list[int]:
    return [item.profile_id for item in items]


def test_filters_list_only_the_criteria_that_were_set():
    assert THREE_CRITERIA.criteria() == ALL
    everything = FeedFilters(5, {"grade": "senior", "format": "remote"}, [1], "RU", True)
    assert everything.criteria() == ["role", "attr:format", "attr:grade", "tags", "country", "timezone"]
    nothing = FeedFilters(None, {}, [], None, False)
    assert nothing.criteria() == []


def test_candidates_are_split_into_exact_and_partial_tiers():
    items = search(
        [
            make_feed_candidate(profile_id=1, matched_filters=ALL),
            make_feed_candidate(profile_id=2, matched_filters=["role", "attr:grade"]),
        ]
    )

    assert [(i.profile_id, i.tier) for i in items] == [(1, FeedTier.EXACT_MATCH), (2, FeedTier.PARTIAL_MATCH)]


def test_a_candidate_with_no_matched_criteria_is_not_ranked_in_search():
    items = search([make_feed_candidate(profile_id=1, matched_filters=[])])

    assert items == []


def test_exact_tier_always_precedes_partial_even_with_a_lower_score():
    weak_exact = make_feed_candidate(profile_id=1, matched_filters=ALL, cos_sim=0.0, tag_ids=[])
    strong_partial = make_feed_candidate(profile_id=2, matched_filters=["role", "attr:grade"], cos_sim=0.9)

    assert ids(search([strong_partial, weak_exact])) == [1, 2]


def test_more_matched_criteria_beat_a_higher_score_in_the_partial_tier():
    two_of_three = make_feed_candidate(
        profile_id=1, matched_filters=["role", "attr:grade"], cos_sim=0.0, tag_ids=[]
    )
    one_of_three = make_feed_candidate(profile_id=2, role_id=6, matched_filters=["tags"], cos_sim=0.95)

    items = search([one_of_three, two_of_three])

    assert ids(items) == [1, 2]
    assert items[0].score < items[1].score


def test_partial_candidates_with_equal_matches_are_ordered_by_score():
    low = make_feed_candidate(profile_id=1, matched_filters=["role"], cos_sim=0.1)
    high = make_feed_candidate(profile_id=2, matched_filters=["role"], cos_sim=0.8)

    assert ids(search([low, high])) == [2, 1]


def test_a_single_criterion_filter_has_no_partial_tier():
    filters = FeedFilters(role_id=5, attributes={}, tag_ids=[], country_code=None, same_timezone=False)

    items = search(
        [make_feed_candidate(profile_id=1, matched_filters=["role"]), make_feed_candidate(profile_id=2)],
        filters=filters,
    )

    assert [(i.profile_id, i.tier) for i in items] == [(1, FeedTier.EXACT_MATCH)]


def test_matched_filters_reason_comes_first_and_lists_matched_and_missed():
    items = search([make_feed_candidate(matched_filters=["role", "tags"])])

    reason = items[0].reasons[0]
    assert reason.code is FeedReasonCode.MATCHED_FILTERS
    assert reason.matched == ["role", "tags"]
    assert reason.missed == ["attr:grade"]


def test_exact_match_reason_has_nothing_missed():
    items = search([make_feed_candidate(matched_filters=ALL)])

    assert items[0].reasons[0].matched == ALL
    assert items[0].reasons[0].missed == []


def test_unknown_match_codes_from_the_query_are_ignored():
    items = search([make_feed_candidate(matched_filters=["role", "something_else"])])

    assert items[0].reasons[0].matched == ["role"]


def test_equal_scores_are_ordered_by_profile_id():
    items = search([make_feed_candidate(profile_id=pid, matched_filters=ALL) for pid in (30, 10, 20)])

    assert ids(items) == [10, 20, 30]


def test_same_timezone_reason_comes_from_the_zone_list():
    items = search(
        [make_feed_candidate(timezone="Europe/Kirov", matched_filters=ALL)],
        zones={"Europe/Moscow", "Europe/Kirov"},
    )

    assert FeedReasonCode.SAME_TIMEZONE in [r.code for r in items[0].reasons]


def test_search_is_cut_to_the_session_cap_keeping_the_best():
    candidates = [make_feed_candidate(profile_id=pid, matched_filters=ALL) for pid in range(1, 11)]

    items = search(candidates, session_cap=3)

    assert ids(items) == [1, 2, 3]


def test_bio_tier_is_needed_only_while_there_is_room():
    ranker = make_ranker(session_cap=2)
    one = ranker.rank_search(
        make_feed_viewer(), THREE_CRITERIA, [make_feed_candidate(profile_id=1, matched_filters=ALL)], FEED_NOW, ()
    )
    two = ranker.rank_search(
        make_feed_viewer(),
        THREE_CRITERIA,
        [make_feed_candidate(profile_id=pid, matched_filters=ALL) for pid in (1, 2, 3)],
        FEED_NOW,
        (),
    )

    assert ranker.needs_bio_tier(one) is True
    assert ranker.needs_bio_tier(two) is False


def test_bio_tier_goes_after_the_cascade_and_skips_duplicates():
    ranker = make_ranker()
    ranked = ranker.rank_search(
        make_feed_viewer(), THREE_CRITERIA, [make_feed_candidate(profile_id=1, matched_filters=ALL)], FEED_NOW, ()
    )
    bio = [
        make_feed_candidate(profile_id=1, cos_sim=0.99),
        make_feed_candidate(profile_id=2, category_id=2, role_id=9, cos_sim=0.4, tag_ids=[]),
        make_feed_candidate(profile_id=3, cos_sim=0.8),
    ]

    items = ranker.append_bio_tier(ranked, make_feed_viewer(), bio, FEED_NOW, ())

    assert [(i.profile_id, i.tier) for i in items] == [
        (1, FeedTier.EXACT_MATCH),
        (3, FeedTier.RECOMMENDED_BY_BIO),
        (2, FeedTier.RECOMMENDED_BY_BIO),
    ]


def test_bio_tier_respects_the_cap():
    ranker = make_ranker(session_cap=2)
    ranked = ranker.rank_search(
        make_feed_viewer(), THREE_CRITERIA, [make_feed_candidate(profile_id=1, matched_filters=ALL)], FEED_NOW, ()
    )

    items = ranker.append_bio_tier(
        ranked, make_feed_viewer(), [make_feed_candidate(profile_id=pid) for pid in (2, 3, 4)], FEED_NOW, ()
    )

    assert ids(items) == [1, 2]


def test_discovery_is_a_single_tier_ordered_by_score_then_id():
    candidates = [
        make_feed_candidate(profile_id=3, cos_sim=0.2),
        make_feed_candidate(profile_id=2, cos_sim=0.9),
        make_feed_candidate(profile_id=1, cos_sim=0.9),
    ]

    items = make_ranker().rank_discovery(make_feed_viewer(), candidates, FEED_NOW, ())

    assert ids(items) == [1, 2, 3]
    assert {item.tier for item in items} == {FeedTier.DISCOVERY}
    assert all(r.code is not FeedReasonCode.MATCHED_FILTERS for item in items for r in item.reasons)


def test_discovery_is_cut_to_the_session_cap():
    candidates = [make_feed_candidate(profile_id=pid) for pid in range(1, 8)]

    items = make_ranker(session_cap=4).rank_discovery(make_feed_viewer(), candidates, FEED_NOW, ())

    assert ids(items) == [1, 2, 3, 4]


def test_ranking_does_not_depend_on_input_order():
    candidates = [
        make_feed_candidate(profile_id=pid, cos_sim=cos, matched_filters=["role"])
        for pid, cos in [(1, 0.3), (2, 0.7), (3, 0.5), (4, 0.7)]
    ]

    forward = ids(search(candidates))
    backward = ids(search(list(reversed(candidates))))

    assert forward == backward == [2, 4, 3, 1]
