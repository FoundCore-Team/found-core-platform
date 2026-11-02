from datetime import timedelta

import pytest

from app.core.config import FeedConfig
from app.domain.enums import FeedReasonCode
from app.domain.services import FeedScorer
from tests.fixtures.factories import FEED_NOW, make_feed_candidate, make_feed_viewer


def make_scorer(config: FeedConfig | None = None) -> FeedScorer:
    config = config or FeedConfig()
    return FeedScorer(
        weight_semantic=config.weight_semantic,
        weight_tags=config.weight_tags,
        weight_role=config.weight_role,
        role_affinity_same_role=config.role_affinity_same_role,
        role_affinity_same_category=config.role_affinity_same_category,
        activity_recent_days=config.activity_recent_days,
        activity_stale_days=config.activity_stale_days,
        activity_factor_recent=config.activity_factor_recent,
        activity_factor_stale=config.activity_factor_stale,
        activity_factor_old=config.activity_factor_old,
        similar_bio_reason_min_cos_sim=config.similar_bio_reason_min_cos_sim,
        max_reason_tags=config.max_reason_tags,
    )


def score_of(candidate, viewer=None, same_utc_offset=False, scorer=None) -> float:
    scorer = scorer or make_scorer()
    score, _ = scorer.score(viewer or make_feed_viewer(), candidate, FEED_NOW, same_utc_offset)
    return score


def codes(reasons) -> list[FeedReasonCode]:
    return [reason.code for reason in reasons]


def test_full_formula_on_a_hand_computed_pair():
    # tag_overlap 3/5 = 0.6, same role 1.0, cos 0.62, active 2 days ago (factor 1.0)
    expected = 0.4 * 0.62 + 0.4 * 0.6 + 0.2 * 1.0

    assert score_of(make_feed_candidate()) == pytest.approx(expected)
    assert expected == pytest.approx(0.688)


def test_tag_overlap_divides_by_the_smaller_tag_set():
    viewer = make_feed_viewer(tag_ids=[1, 2])
    candidate = make_feed_candidate(tag_ids=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], cos_sim=0.0)

    # overlap 2/min(2, 10) = 1.0; role 1.0
    assert score_of(candidate, viewer) == pytest.approx(0.4 * 1.0 + 0.2 * 1.0)


def test_tag_overlap_is_zero_when_either_side_has_no_tags():
    viewer = make_feed_viewer(tag_ids=[])

    assert score_of(make_feed_candidate(cos_sim=0.0), viewer) == pytest.approx(0.2)
    assert score_of(make_feed_candidate(cos_sim=0.0, tag_ids=[])) == pytest.approx(0.2)


def test_duplicate_tag_ids_do_not_inflate_the_overlap():
    candidate = make_feed_candidate(tag_ids=[3, 3, 3, 4, 4], cos_sim=0.0)

    # distinct shared {3, 4} / min(5, 2) = 1.0
    assert score_of(candidate) == pytest.approx(0.4 * 1.0 + 0.2 * 1.0)


@pytest.mark.parametrize(
    ("role_id", "category_id", "affinity"),
    [(5, 1, 1.0), (6, 1, 0.5), (9, 2, 0.0)],
)
def test_role_affinity(role_id, category_id, affinity):
    candidate = make_feed_candidate(role_id=role_id, category_id=category_id, cos_sim=0.0, tag_ids=[])

    assert score_of(candidate) == pytest.approx(0.2 * affinity)


@pytest.mark.parametrize(
    ("age", "factor"),
    [
        (timedelta(days=0), 1.0),
        (timedelta(days=7), 1.0),
        (timedelta(days=7, seconds=1), 0.8),
        (timedelta(days=30), 0.8),
        (timedelta(days=30, seconds=1), 0.5),
        (timedelta(days=400), 0.5),
    ],
)
def test_activity_factor_boundaries(age, factor):
    candidate = make_feed_candidate(cos_sim=0.0, tag_ids=[], last_active_at=FEED_NOW - age)

    # Only the role term (0.2) is left, scaled by the activity factor.
    assert score_of(candidate) == pytest.approx(0.2 * factor)


def test_without_a_vector_the_semantic_weight_is_split_between_tags_and_role():
    candidate = make_feed_candidate(cos_sim=None)

    # weights 0.4/0.2 -> 2/3 and 1/3; overlap 0.6, role 1.0
    assert score_of(candidate) == pytest.approx((2 / 3) * 0.6 + (1 / 3) * 1.0)


def test_degraded_weights_still_sum_to_one():
    candidate = make_feed_candidate(cos_sim=None, tag_ids=[1, 2, 3, 4, 5])

    assert score_of(candidate) == pytest.approx(1.0)


def test_degradation_follows_custom_weights():
    scorer = make_scorer(FeedConfig(weight_semantic=0.5, weight_tags=0.25, weight_role=0.25))
    candidate = make_feed_candidate(cos_sim=None, tag_ids=[1, 2, 3, 4, 5])

    assert score_of(candidate, scorer=scorer) == pytest.approx(1.0)
    assert score_of(make_feed_candidate(cos_sim=None, tag_ids=[]), scorer=scorer) == pytest.approx(0.5)


@pytest.mark.parametrize(("cos_sim", "expected_cos"), [(-0.4, 0.0), (1.0000001, 1.0)])
def test_cosine_is_clamped_into_zero_one(cos_sim, expected_cos):
    candidate = make_feed_candidate(cos_sim=cos_sim, tag_ids=[])

    assert score_of(candidate) == pytest.approx(0.4 * expected_cos + 0.2)


def test_stub_like_zero_cosine_does_not_redistribute_weights():
    # A real (even if useless) vector pair keeps the 0.4 weight on the semantic term.
    assert score_of(make_feed_candidate(cos_sim=0.0, tag_ids=[])) == pytest.approx(0.2)


def test_reasons_for_a_strong_match_come_in_a_fixed_order():
    _, reasons = make_scorer().score(make_feed_viewer(), make_feed_candidate(), FEED_NOW, True)

    assert codes(reasons) == [
        FeedReasonCode.SHARED_TAGS,
        FeedReasonCode.SAME_ROLE,
        FeedReasonCode.SAME_COUNTRY,
        FeedReasonCode.SAME_TIMEZONE,
        FeedReasonCode.SIMILAR_BIO,
        FeedReasonCode.RECENTLY_ACTIVE,
    ]
    assert reasons[0].tag_ids == [3, 4, 5]


def test_reasons_are_empty_for_a_stranger():
    candidate = make_feed_candidate(
        role_id=9,
        category_id=2,
        country_code="DE",
        tag_ids=[90, 91],
        cos_sim=0.1,
        last_active_at=FEED_NOW - timedelta(days=90),
    )

    _, reasons = make_scorer().score(make_feed_viewer(), candidate, FEED_NOW, False)

    assert reasons == []


def test_same_timezone_reason_follows_the_flag_not_the_zone_name():
    candidate = make_feed_candidate(timezone="Europe/Kirov")

    _, with_flag = make_scorer().score(make_feed_viewer(), candidate, FEED_NOW, True)
    _, without_flag = make_scorer().score(make_feed_viewer(), candidate, FEED_NOW, False)

    assert FeedReasonCode.SAME_TIMEZONE in codes(with_flag)
    assert FeedReasonCode.SAME_TIMEZONE not in codes(without_flag)


def test_shared_tags_reason_is_sorted_and_capped():
    viewer = make_feed_viewer(tag_ids=list(range(20, 0, -1)))
    candidate = make_feed_candidate(tag_ids=list(range(1, 21)))

    _, reasons = make_scorer().score(viewer, candidate, FEED_NOW, False)

    assert reasons[0].code is FeedReasonCode.SHARED_TAGS
    assert reasons[0].tag_ids == [1, 2, 3, 4, 5]


def test_similar_bio_reason_needs_a_vector_and_the_threshold():
    scorer = make_scorer()
    viewer = make_feed_viewer()

    _, at_threshold = scorer.score(viewer, make_feed_candidate(cos_sim=0.5), FEED_NOW, False)
    _, below = scorer.score(viewer, make_feed_candidate(cos_sim=0.49), FEED_NOW, False)
    _, no_vector = scorer.score(viewer, make_feed_candidate(cos_sim=None), FEED_NOW, False)

    assert FeedReasonCode.SIMILAR_BIO in codes(at_threshold)
    assert FeedReasonCode.SIMILAR_BIO not in codes(below)
    assert FeedReasonCode.SIMILAR_BIO not in codes(no_vector)


def test_recently_active_reason_uses_the_recent_boundary():
    scorer = make_scorer()
    viewer = make_feed_viewer()

    _, on_edge = scorer.score(
        viewer, make_feed_candidate(last_active_at=FEED_NOW - timedelta(days=7)), FEED_NOW, False
    )
    _, past_edge = scorer.score(
        viewer, make_feed_candidate(last_active_at=FEED_NOW - timedelta(days=8)), FEED_NOW, False
    )

    assert FeedReasonCode.RECENTLY_ACTIVE in codes(on_edge)
    assert FeedReasonCode.RECENTLY_ACTIVE not in codes(past_edge)
