from datetime import datetime, timezone

import pytest

from app.application.services.feed_filter_validator import FeedFilterValidator
from app.application.services.feed_viewer_resolver import FeedViewerResolver
from app.application.services.rate_limit_guard import RateLimitGuard
from app.application.services.timezone_catalog import TimezoneCatalog
from app.application.use_cases import GetFeedCardUseCase, GetFeedUseCase, RecordProfileViewUseCase
from app.core.config import FeedConfig
from app.domain.entities import (
    FeedCursor,
    FeedFilters,
    FeedRankedItem,
    FeedSession,
    ProfileEmbeddingEntity,
    RoleEntity,
    RoleFieldEntity,
    TagEntity,
)
from app.domain.enums import (
    AnalyticsEvent,
    EmbeddingStatus,
    FeedEndReason,
    FeedMode,
    FeedReasonCode,
    FeedTier,
    ProfileStatus,
    RateLimitAction,
    RoleFieldType,
    TagStatus,
)
from app.domain.exceptions import (
    FeedProfileNotFoundError,
    InvalidFeedCursorError,
    InvalidFeedFilterError,
    NoActiveProfileError,
    RateLimitExceededError,
)
from app.domain.services import FeedRanker, build_feed_params_hash
from tests.fixtures.factories import make_feed_candidate, make_feed_card, make_profile_entity, make_user_entity
from tests.fixtures.fake_analytics import FakeAnalyticsTracker
from tests.fixtures.fake_feed import FakeFeedRepository, FakeFeedSessionRepository, FakeProfileViewRepository
from tests.fixtures.fake_profile_embedding_repository import FakeProfileEmbeddingRepository
from tests.fixtures.fake_profile_repository import FakeProfileRepository
from tests.fixtures.fake_rate_limiter import FakeRateLimiter
from tests.fixtures.fake_taxonomy import FakeTaxonomyRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.unit.test_feed_scorer import make_scorer

MODEL = "stub-v1"
STAMP = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
NO_FILTERS = FeedFilters(role_id=None, attributes={}, tag_ids=[], country_code=None, same_timezone=False)
ROLE_AND_TAG = FeedFilters(role_id=5, attributes={}, tag_ids=[42], country_code=None, same_timezone=False)
DISCOVERY_HASH = build_feed_params_hash(1, FeedMode.DISCOVERY, NO_FILTERS)


def _select_field(field_id: int, key: str, option: str) -> RoleFieldEntity:
    return RoleFieldEntity(
        id=field_id,
        role_id=5,
        key=key,
        label=key,
        field_type=RoleFieldType.SELECT,
        options=[{"value": option, "label": option}],
        is_required=False,
        is_filterable=True,
    )


def ranked_candidates(count: int, start: int = 101, **overrides):
    """Candidates whose score falls with their id, so the ranking order is the id order."""
    return [
        make_feed_candidate(profile_id=start + n, cos_sim=0.9 - 0.1 * n, **overrides) for n in range(count)
    ]


class Env:
    def __init__(
        self,
        *,
        discovery=None,
        search=None,
        bio=None,
        extra_candidates=None,
        has_vector: bool = True,
        active: bool = True,
        profile_status: ProfileStatus = ProfileStatus.ACTIVE,
        session_cap: int = 200,
    ) -> None:
        self.user = make_user_entity(id=1, active_profile_id=1 if active else None)
        profile = make_profile_entity(
            id=1, user_id=1, category_id=1, role_id=5, status=profile_status, timezone="Europe/Moscow", updated_at=STAMP
        )
        self.profiles = FakeProfileRepository(profiles=[profile])
        self.embeddings = FakeProfileEmbeddingRepository(
            [ProfileEmbeddingEntity(1, "hash", MODEL, EmbeddingStatus.READY, STAMP, STAMP)] if has_vector else []
        )
        self.taxonomy = FakeTaxonomyRepository(
            roles=[RoleEntity(id=5, category_id=1, slug="engineering", title="Engineering")],
            role_fields=[_select_field(500, "workload", "full_time"), _select_field(501, "grade", "senior")],
            tags=[
                TagEntity(42, "Go", "go", TagStatus.APPROVED, None),
                TagEntity(43, "Zig", "zig", TagStatus.PENDING, 7),
            ],
        )
        every = [*(discovery or []), *(search or []), *(bio or []), *(extra_candidates or [])]
        self.feed = FakeFeedRepository(
            discovery=discovery,
            search=search,
            bio=bio,
            candidates=extra_candidates,
            cards=[make_feed_card(c.profile_id) for c in every],
        )
        self.sessions = FakeFeedSessionRepository()
        self.limiter = FakeRateLimiter()
        self.views = FakeProfileViewRepository()
        self.uow = FakeUnitOfWork()
        self.analytics = FakeAnalyticsTracker()

        resolver = FeedViewerResolver(self.profiles, self.embeddings, MODEL)
        catalog = TimezoneCatalog()
        self.get_feed = GetFeedUseCase(
            viewer_resolver=resolver,
            filter_validator=FeedFilterValidator(self.taxonomy, catalog, []),
            feed_repository=self.feed,
            session_repository=self.sessions,
            rate_limit_guard=RateLimitGuard(self.limiter),
            ranker=FeedRanker(make_scorer(FeedConfig()), session_cap),
            timezone_catalog=catalog,
            current_embedding_model=MODEL,
            scoring_fingerprint="fingerprint",
            bio_tier_min_cos_sim=0.35,
            analytics_tracker=self.analytics,
        )
        self.get_card = GetFeedCardUseCase(resolver, self.feed, make_scorer(FeedConfig()), catalog, MODEL)
        self.record_view = RecordProfileViewUseCase(
            resolver, self.feed, self.views, self.sessions, self.uow, self.analytics
        )

    def events(self, event: AnalyticsEvent) -> list:
        return [tracked for tracked in self.analytics.events if tracked.event is event]

    async def discovery(self, cursor=None, limit=20):
        return await self.get_feed.execute(self.user, FeedMode.DISCOVERY, NO_FILTERS, cursor, limit)

    async def search(self, filters=ROLE_AND_TAG, cursor=None, limit=20):
        return await self.get_feed.execute(self.user, FeedMode.SEARCH, filters, cursor, limit)


def ids(page) -> list[int]:
    return [item.card.profile.id for item in page.items]


# ---------- who may use the feed ----------


@pytest.mark.parametrize(
    "env_kwargs",
    [
        dict(active=False),
        dict(profile_status=ProfileStatus.PAUSED),
        dict(profile_status=ProfileStatus.HIDDEN_BY_ADMIN),
    ],
)
async def test_without_an_active_profile_the_feed_is_closed(env_kwargs):
    env = Env(discovery=ranked_candidates(2), **env_kwargs)

    with pytest.raises(NoActiveProfileError):
        await env.discovery()
    with pytest.raises(NoActiveProfileError):
        await env.get_card.execute(env.user, 101)
    with pytest.raises(NoActiveProfileError):
        await env.record_view.execute(env.user, 101)

    assert env.feed.calls == []
    assert env.uow.commits == 0


async def test_a_pointer_to_somebody_elses_profile_is_not_an_active_profile():
    env = Env(discovery=ranked_candidates(1))
    env.user.active_profile_id = 999

    with pytest.raises(NoActiveProfileError):
        await env.discovery()


# ---------- sessions ----------


async def test_a_miss_ranks_the_candidates_and_stores_the_session():
    env = Env(discovery=ranked_candidates(3))

    page = await env.discovery()

    assert ids(page) == [101, 102, 103]
    assert {item.tier for item in page.items} == {FeedTier.DISCOVERY}
    assert all(type(item.match_score) is int and 0 <= item.match_score <= 100 for item in page.items)
    assert env.feed.calls.count("discovery") == 1
    assert len(env.sessions.sets) == 1


async def test_a_hit_does_not_touch_the_candidate_queries():
    env = Env(discovery=ranked_candidates(3))

    first = await env.discovery()
    second = await env.discovery()

    assert ids(first) == ids(second)
    assert env.feed.calls.count("discovery") == 1
    assert len(env.sessions.sets) == 1


async def test_a_miss_is_counted_once_against_the_session_build_limit_of_the_user():
    env = Env(discovery=ranked_candidates(3))

    await env.discovery()

    assert env.limiter.hits == [(RateLimitAction.FEED_SESSION_BUILD, 1)]


async def test_a_hit_and_the_following_pages_are_not_counted():
    env = Env(discovery=ranked_candidates(5))
    first = await env.discovery(limit=2)
    env.limiter.hits.clear()

    await env.discovery(cursor=first.next_cursor, limit=2)
    await env.discovery()

    assert env.limiter.hits == []


async def test_an_expired_session_named_by_a_cursor_is_counted_again():
    env = Env(discovery=ranked_candidates(5))
    first = await env.discovery(limit=2)
    env.sessions.expire()
    env.limiter.hits.clear()

    await env.discovery(cursor=first.next_cursor, limit=2)

    assert env.limiter.hits == [(RateLimitAction.FEED_SESSION_BUILD, 1)]


async def test_a_refused_build_runs_no_candidate_query_and_stores_no_session():
    env = Env(discovery=ranked_candidates(3))
    env.limiter.denied = {RateLimitAction.FEED_SESSION_BUILD}

    with pytest.raises(RateLimitExceededError):
        await env.discovery()

    assert env.feed.calls == []
    assert env.sessions.sets == []


async def test_a_refused_build_does_not_stop_a_live_session():
    env = Env(discovery=ranked_candidates(3))
    await env.discovery()
    env.limiter.denied = {RateLimitAction.FEED_SESSION_BUILD}

    page = await env.discovery()

    assert ids(page) == [101, 102, 103]


async def test_the_feed_works_while_the_session_store_is_down():
    env = Env(discovery=ranked_candidates(3))
    env.sessions.outage = True

    first = await env.discovery()
    second = await env.discovery()

    assert ids(first) == ids(second) == [101, 102, 103]
    assert env.feed.calls.count("discovery") == 2


async def test_pages_follow_each_other_without_gaps_or_repeats():
    env = Env(discovery=ranked_candidates(5))

    first = await env.discovery(limit=2)
    second = await env.discovery(cursor=first.next_cursor, limit=2)
    third = await env.discovery(cursor=second.next_cursor, limit=2)

    assert [ids(first), ids(second), ids(third)] == [[101, 102], [103, 104], [105]]
    assert [first.has_more, second.has_more, third.has_more] == [True, True, False]
    assert third.next_cursor is None and third.end_reason is FeedEndReason.END_OF_FEED
    assert first.end_reason is None and second.end_reason is None
    assert env.feed.calls.count("discovery") == 1


async def test_a_page_that_ends_exactly_on_the_last_item_is_the_end_of_the_feed():
    env = Env(discovery=ranked_candidates(4))

    first = await env.discovery(limit=2)
    second = await env.discovery(cursor=first.next_cursor, limit=2)

    assert ids(second) == [103, 104]
    assert second.has_more is False and second.end_reason is FeedEndReason.END_OF_FEED


async def test_an_empty_feed_says_nobody_yet_and_is_not_an_error():
    env = Env(discovery=[])

    page = await env.discovery()

    assert page.items == [] and page.has_more is False and page.next_cursor is None
    assert page.end_reason is FeedEndReason.NO_CANDIDATES_YET


async def test_a_cursor_past_the_end_is_the_end_of_the_feed_not_nobody_yet():
    env = Env(discovery=ranked_candidates(2))
    first = await env.discovery()

    page = await env.discovery(cursor=FeedCursor(50, DISCOVERY_HASH, env.sessions.keys[0]))

    assert first.end_reason is FeedEndReason.END_OF_FEED
    assert page.items == [] and page.end_reason is FeedEndReason.END_OF_FEED


async def test_a_cursor_with_other_parameters_is_refused():
    env = Env(discovery=ranked_candidates(3))
    first = await env.discovery(limit=1)
    stale = FeedCursor(first.next_cursor.offset, "0" * 64, first.next_cursor.session_key)

    with pytest.raises(InvalidFeedCursorError):
        await env.discovery(cursor=stale)


async def test_scrolling_survives_a_change_of_the_current_session_key():
    env = Env(discovery=ranked_candidates(5), has_vector=False)
    first = await env.discovery(limit=2)
    # The viewer's own vector arrives while they scroll: the current key changes, the session of the cursor lives on.
    await env.embeddings.mark_pending(1, STAMP)
    await env.embeddings.save(1, [0.1], "hash", MODEL, STAMP)

    second = await env.discovery(cursor=first.next_cursor, limit=2)

    assert ids(second) == [103, 104]
    assert env.feed.calls.count("discovery") == 1
    assert second.next_cursor.session_key == first.next_cursor.session_key


async def test_an_expired_session_is_rebuilt_and_sliced_from_the_same_offset():
    env = Env(discovery=ranked_candidates(5))
    first = await env.discovery(limit=2)
    env.sessions.expire()

    second = await env.discovery(cursor=first.next_cursor, limit=2)

    assert ids(second) == [103, 104]
    assert env.feed.calls.count("discovery") == 2


@pytest.mark.parametrize(
    ("owner", "params_hash"),
    [(99, DISCOVERY_HASH), (1, "f" * 64)],
    ids=["another viewer", "another parameter set"],
)
async def test_a_session_of_somebody_else_named_by_the_cursor_is_never_served(owner, params_hash):
    env = Env(discovery=ranked_candidates(2), extra_candidates=[make_feed_candidate(profile_id=555)])
    foreign = FeedSession(owner, params_hash, [FeedRankedItem(555, FeedTier.DISCOVERY, 1.0, [])], STAMP)
    env.sessions.put("a" * 64, foreign)

    page = await env.discovery(cursor=FeedCursor(0, DISCOVERY_HASH, "a" * 64))

    assert ids(page) == [101, 102]
    assert env.feed.calls.count("discovery") == 1


async def test_a_profile_that_stopped_being_visible_drops_out_of_the_page():
    env = Env(discovery=ranked_candidates(5))
    await env.discovery(limit=3)
    env.feed.remove_card(102)

    page = await env.discovery(limit=3)

    assert ids(page) == [101, 103]
    # The session is not rebuilt for one missing profile, so paging goes on as if it were still there.
    assert page.has_more is True and page.next_cursor.offset == 3
    assert env.feed.calls.count("discovery") == 1


async def test_viewed_profiles_keep_their_place_and_are_flagged():
    env = Env(discovery=ranked_candidates(3))
    env.feed.viewed.add((1, 102))

    page = await env.discovery()

    assert ids(page) == [101, 102, 103]
    assert [item.card.is_viewed for item in page.items] == [False, True, False]


# ---------- search ----------


async def test_search_with_an_invalid_filter_is_refused_before_any_query():
    env = Env()
    wrong_role = FeedFilters(role_id=999, attributes={}, tag_ids=[], country_code=None, same_timezone=False)
    pending_tag = FeedFilters(role_id=None, attributes={}, tag_ids=[43], country_code=None, same_timezone=False)

    for filters in (wrong_role, pending_tag):
        with pytest.raises(InvalidFeedFilterError):
            await env.search(filters)

    assert env.feed.calls == []


async def test_search_without_any_criterion_is_refused():
    with pytest.raises(InvalidFeedFilterError) as error:
        await Env().search(NO_FILTERS)

    assert error.value.reason == "filters_required"


async def test_search_orders_exact_before_partial_and_explains_why():
    exact = make_feed_candidate(profile_id=101, matched_filters=["role", "tags"], cos_sim=0.1)
    partial = make_feed_candidate(profile_id=102, matched_filters=["role"], cos_sim=0.9)
    env = Env(search=[partial, exact])

    page = await env.search()

    assert ids(page) == [101, 102]
    assert [item.tier for item in page.items] == [FeedTier.EXACT_MATCH, FeedTier.PARTIAL_MATCH]
    first_reason = page.items[1].reasons[0]
    assert first_reason.code is FeedReasonCode.MATCHED_FILTERS
    assert (first_reason.matched, first_reason.missed) == (["role"], ["tags"])


async def test_the_bio_tier_is_queried_after_the_cascade_and_excludes_what_is_already_ranked():
    env = Env(
        search=[make_feed_candidate(profile_id=101, matched_filters=["role", "tags"])],
        bio=[make_feed_candidate(profile_id=201, category_id=2, role_id=9, cos_sim=0.6)],
    )

    page = await env.search()

    assert ids(page) == [101, 201]
    assert page.items[1].tier is FeedTier.RECOMMENDED_BY_BIO
    assert env.feed.bio_args == [{"exclude": [101], "min_cos_sim": 0.35}]


async def test_the_bio_tier_is_skipped_without_a_viewer_vector():
    env = Env(
        search=[make_feed_candidate(profile_id=101, matched_filters=["role"])],
        bio=[make_feed_candidate(profile_id=201, cos_sim=0.9)],
        has_vector=False,
    )

    page = await env.search()

    assert ids(page) == [101]
    assert "bio" not in env.feed.calls


async def test_the_bio_tier_is_skipped_when_the_cascade_already_fills_the_cap():
    env = Env(
        search=[make_feed_candidate(profile_id=pid, matched_filters=["role", "tags"]) for pid in (101, 102)],
        bio=[make_feed_candidate(profile_id=201, cos_sim=0.9)],
        session_cap=2,
    )

    page = await env.search()

    assert ids(page) == [101, 102]
    assert "bio" not in env.feed.calls


async def test_the_timezone_filter_passes_every_zone_with_the_viewers_current_offset():
    env = Env(search=[make_feed_candidate(profile_id=101, matched_filters=["timezone"])])
    with_timezone = FeedFilters(None, {}, [], None, same_timezone=True)

    await env.search(with_timezone)
    await env.search(ROLE_AND_TAG)

    zone_ids = env.feed.search_args[0]["timezone_ids"]
    assert {"Europe/Moscow", "Europe/Kirov", "Europe/Istanbul"} <= set(zone_ids)
    assert env.feed.search_args[1]["timezone_ids"] == []


async def test_each_set_of_filters_gets_its_own_session():
    env = Env(search=[make_feed_candidate(profile_id=101, matched_filters=["role", "tags"])])
    other = FeedFilters(role_id=5, attributes={}, tag_ids=[], country_code=None, same_timezone=False)

    await env.search(ROLE_AND_TAG)
    await env.search(other)
    await env.search(ROLE_AND_TAG)

    assert env.feed.calls.count("search") == 2
    assert len(env.sessions.keys) == 2


# ---------- single card ----------


async def test_a_card_by_id_has_a_score_and_no_tier():
    env = Env(extra_candidates=[make_feed_candidate(profile_id=777, category_id=2, role_id=9, cos_sim=0.5)])

    item = await env.get_card.execute(env.user, 777)

    assert item.tier is None
    assert item.card.profile.id == 777
    assert 0 <= item.match_score <= 100


@pytest.mark.parametrize("hidden", ["no candidate", "no card"])
async def test_a_card_that_is_not_visible_is_not_found(hidden):
    env = Env(extra_candidates=[make_feed_candidate(profile_id=777)])
    if hidden == "no card":
        env.feed.remove_card(777)
        target = 777
    else:
        target = 888

    with pytest.raises(FeedProfileNotFoundError):
        await env.get_card.execute(env.user, target)


# ---------- views ----------


async def test_opening_a_profile_records_one_view_under_the_active_profile_and_commits():
    env = Env(discovery=ranked_candidates(1))

    await env.record_view.execute(env.user, 101)

    assert set(env.views.views) == {(1, 101)}
    assert env.uow.commits == 1


async def test_opening_a_profile_twice_keeps_the_first_view():
    env = Env(discovery=ranked_candidates(1))

    await env.record_view.execute(env.user, 101)
    first_time = env.views.views[(1, 101)]
    await env.record_view.execute(env.user, 101)

    assert env.views.views == {(1, 101): first_time}


async def test_a_profile_that_is_not_visible_cannot_be_marked_as_viewed():
    env = Env(discovery=ranked_candidates(1))

    with pytest.raises(FeedProfileNotFoundError):
        await env.record_view.execute(env.user, 999)

    assert env.views.add_calls == 0
    assert env.uow.commits == 0


# ---------- feed events ----------

NO_FILTER_FLAGS = {
    "filters_count": 0,
    "filter_role": False,
    "filter_tags": False,
    "filter_country": False,
    "filter_timezone": False,
    "filter_attrs": None,
}


async def test_the_first_page_reports_the_ranking_and_the_page():
    env = Env(discovery=ranked_candidates(3))

    await env.discovery(limit=2)

    assert [event.event for event in env.analytics.events] == [
        AnalyticsEvent.FEED_RANKED,
        AnalyticsEvent.FEED_PAGE_SERVED,
    ]
    ranked, served = env.analytics.events
    assert ranked.user_id == served.user_id == 1
    assert ranked.properties == {
        "actor_profile_id": 1,
        "mode": "discovery",
        **NO_FILTER_FLAGS,
        "candidates_count": 3,
        "exact_count": 0,
        "partial_count": 0,
        "bio_count": 0,
    }
    assert served.properties == {
        "actor_profile_id": 1,
        "mode": "discovery",
        "offset": 0,
        "items_count": 2,
        "has_more": True,
        "is_reranked": True,
    }


async def test_a_page_from_a_live_session_is_not_a_ranking_and_the_last_page_ends_the_feed():
    env = Env(discovery=ranked_candidates(3))
    first = await env.discovery(limit=2)
    env.analytics.events.clear()

    await env.discovery(cursor=first.next_cursor, limit=2)

    assert [event.event for event in env.analytics.events] == [
        AnalyticsEvent.FEED_PAGE_SERVED,
        AnalyticsEvent.FEED_EMPTY,
    ]
    served, empty = env.analytics.events
    assert (served.properties["offset"], served.properties["items_count"]) == (2, 1)
    assert (served.properties["has_more"], served.properties["is_reranked"]) == (False, False)
    assert empty.properties == {"actor_profile_id": 1, "mode": "discovery", "reason": "END_OF_FEED"}


async def test_nobody_yet_is_reported_when_the_feed_is_ranked_and_not_again_from_the_live_session():
    env = Env(discovery=[])

    await env.discovery()
    await env.discovery()

    [empty] = env.events(AnalyticsEvent.FEED_EMPTY)
    assert empty.properties["reason"] == "NO_CANDIDATES_YET"
    assert [event.properties["is_reranked"] for event in env.events(AnalyticsEvent.FEED_PAGE_SERVED)] == [True, False]


async def test_a_search_ranking_reports_which_criteria_were_set_and_how_the_tiers_split():
    every_criterion = ["role", "attr:grade", "attr:workload", "tags", "country", "timezone"]
    env = Env(
        search=[
            make_feed_candidate(profile_id=101, matched_filters=every_criterion),
            make_feed_candidate(profile_id=102, matched_filters=["role"]),
        ],
        bio=[make_feed_candidate(profile_id=201, category_id=2, role_id=9, cos_sim=0.6)],
    )
    filters = FeedFilters(
        role_id=5,
        attributes={"workload": "full_time", "grade": "senior"},
        tag_ids=[42],
        country_code="DE",
        same_timezone=True,
    )

    await env.search(filters)

    [ranked] = env.events(AnalyticsEvent.FEED_RANKED)
    assert ranked.properties == {
        "actor_profile_id": 1,
        "mode": "search",
        "filters_count": 6,
        "filter_role": True,
        "filter_tags": True,
        "filter_country": True,
        "filter_timezone": True,
        # Keys sorted, values left out.
        "filter_attrs": "grade,workload",
        "candidates_count": 3,
        "exact_count": 1,
        "partial_count": 1,
        "bio_count": 1,
    }
    assert "senior" not in ranked.properties.values() and "full_time" not in ranked.properties.values()


async def test_a_refused_or_invalid_feed_request_sends_nothing():
    env = Env(discovery=ranked_candidates(3))
    env.limiter.denied = {RateLimitAction.FEED_SESSION_BUILD}
    with pytest.raises(RateLimitExceededError):
        await env.discovery()

    with pytest.raises(InvalidFeedFilterError):
        await env.search(FeedFilters(role_id=999, attributes={}, tag_ids=[], country_code=None, same_timezone=False))

    assert env.analytics.events == []


async def test_every_page_points_the_viewer_at_the_session_it_was_cut_from():
    env = Env(discovery=ranked_candidates(3))

    first = await env.discovery(limit=2)
    assert env.sessions.last_session_keys == {1: first.next_cursor.session_key}

    env.sessions.last_session_keys.clear()
    await env.discovery(cursor=first.next_cursor, limit=2)
    assert env.sessions.last_session_keys == {1: first.next_cursor.session_key}


# ---------- profile view events ----------


async def test_a_view_from_the_feed_carries_the_tier_and_the_score_the_card_showed():
    env = Env(discovery=ranked_candidates(3))
    page = await env.discovery()
    shown = next(item for item in page.items if item.card.profile.id == 102)

    await env.record_view.execute(env.user, 102)

    [viewed] = env.events(AnalyticsEvent.PROFILE_VIEWED)
    assert viewed.user_id == 1
    assert viewed.properties == {
        "actor_profile_id": 1,
        "target_profile_id": 102,
        "match_type": "DISCOVERY",
        "match_score": shown.match_score,
        "is_first_view": True,
    }


async def test_every_view_is_reported_and_only_the_first_one_is_marked_first():
    env = Env(discovery=ranked_candidates(1))
    await env.discovery()

    await env.record_view.execute(env.user, 101)
    await env.record_view.execute(env.user, 101)

    assert [event.properties["is_first_view"] for event in env.events(AnalyticsEvent.PROFILE_VIEWED)] == [True, False]


async def test_a_view_without_a_feed_page_before_it_has_no_tier():
    env = Env(discovery=ranked_candidates(1))

    await env.record_view.execute(env.user, 101)

    [viewed] = env.events(AnalyticsEvent.PROFILE_VIEWED)
    assert (viewed.properties["match_type"], viewed.properties["match_score"]) == (None, None)


async def test_a_view_of_a_card_that_is_not_in_the_last_feed_page_has_no_tier():
    # Visible, but not ranked in this session: opened from a notification or by id.
    env = Env(discovery=ranked_candidates(1), extra_candidates=[make_feed_candidate(profile_id=777)])
    await env.discovery()

    await env.record_view.execute(env.user, 777)

    assert env.events(AnalyticsEvent.PROFILE_VIEWED)[0].properties["match_type"] is None


async def test_a_pointer_to_a_session_of_another_viewer_is_not_trusted():
    env = Env(discovery=ranked_candidates(1))
    other = FeedSession(
        viewer_profile_id=999,
        params_hash="p" * 64,
        items=[FeedRankedItem(101, FeedTier.EXACT_MATCH, 0.9, [])],
        built_at=STAMP,
    )
    env.sessions.put("s" * 64, other)
    env.sessions.last_session_keys[1] = "s" * 64

    await env.record_view.execute(env.user, 101)

    assert env.events(AnalyticsEvent.PROFILE_VIEWED)[0].properties["match_type"] is None


async def test_a_view_while_redis_is_down_is_recorded_and_reported_without_a_tier():
    env = Env(discovery=ranked_candidates(1))
    await env.discovery()
    env.sessions.outage = True

    await env.record_view.execute(env.user, 101)

    assert set(env.views.views) == {(1, 101)}
    [viewed] = env.events(AnalyticsEvent.PROFILE_VIEWED)
    assert (viewed.properties["match_type"], viewed.properties["is_first_view"]) == (None, True)


async def test_a_view_whose_commit_fails_sends_nothing():
    env = Env(discovery=ranked_candidates(1))
    env.uow.fail_on_commit = True

    with pytest.raises(RuntimeError):
        await env.record_view.execute(env.user, 101)

    assert env.events(AnalyticsEvent.PROFILE_VIEWED) == []


async def test_a_view_of_an_invisible_profile_sends_nothing():
    env = Env(discovery=ranked_candidates(1))

    with pytest.raises(FeedProfileNotFoundError):
        await env.record_view.execute(env.user, 999)

    assert env.events(AnalyticsEvent.PROFILE_VIEWED) == []
