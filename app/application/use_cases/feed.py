from collections import Counter
from datetime import datetime, timezone

from app.application.services.feed_filter_validator import FeedFilterValidator
from app.application.services.feed_viewer_resolver import FeedViewerResolver
from app.application.services.rate_limit_guard import RateLimitGuard
from app.application.services.timezone_catalog import TimezoneCatalog
from app.domain.constants import FEED_ALGORITHM_VERSION
from app.domain.entities import (
    FeedCursor,
    FeedFilters,
    FeedPage,
    FeedPageItem,
    FeedRankedItem,
    FeedSession,
    FeedViewer,
    UserEntity,
)
from app.domain.enums import AnalyticsEvent, FeedEndReason, FeedMode, FeedTier, RateLimitAction
from app.domain.exceptions import FeedProfileNotFoundError, InvalidFeedCursorError, InvalidFeedFilterError
from app.domain.interfaces import (
    IAnalyticsTracker,
    IFeedRepository,
    IFeedSessionRepository,
    IProfileViewRepository,
    IUnitOfWork,
)
from app.domain.services import FeedRanker, FeedScorer, build_feed_params_hash, build_feed_session_key


class GetFeedUseCase:
    """One page of the feed. The ranking is built once per session and then only sliced (see CLAUDE.md, "Feed")."""

    def __init__(
        self,
        viewer_resolver: FeedViewerResolver,
        filter_validator: FeedFilterValidator,
        feed_repository: IFeedRepository,
        session_repository: IFeedSessionRepository,
        rate_limit_guard: RateLimitGuard,
        ranker: FeedRanker,
        timezone_catalog: TimezoneCatalog,
        current_embedding_model: str,
        scoring_fingerprint: str,
        bio_tier_min_cos_sim: float,
        analytics_tracker: IAnalyticsTracker,
    ) -> None:
        self._viewer_resolver = viewer_resolver
        self._filter_validator = filter_validator
        self._feed_repository = feed_repository
        self._session_repository = session_repository
        self._rate_limit_guard = rate_limit_guard
        self._ranker = ranker
        self._timezone_catalog = timezone_catalog
        self._current_embedding_model = current_embedding_model
        self._scoring_fingerprint = scoring_fingerprint
        self._bio_tier_min_cos_sim = bio_tier_min_cos_sim
        self._analytics_tracker = analytics_tracker

    async def _build_session(
        self, viewer: FeedViewer, mode: FeedMode, filters: FeedFilters, params_hash: str, now: datetime
    ) -> FeedSession:
        # The same current UTC offset as the viewer's zone, computed here because domain does not know tzdata.
        same_offset_zone_ids = self._timezone_catalog.zone_ids_with_same_offset(viewer.timezone, now)
        model = self._current_embedding_model

        if mode is FeedMode.DISCOVERY:
            candidates = await self._feed_repository.list_discovery_candidates(viewer, model)
            items = self._ranker.rank_discovery(viewer, candidates, now, same_offset_zone_ids)
        else:
            candidates = await self._feed_repository.list_search_candidates(
                viewer, filters, same_offset_zone_ids if filters.same_timezone else [], model
            )
            items = self._ranker.rank_search(viewer, filters, candidates, now, same_offset_zone_ids)
            # The bio tier scans vectors of every category, so it is queried only when it can still add something.
            if viewer.has_comparable_vector and self._ranker.needs_bio_tier(items):
                bio_candidates = await self._feed_repository.list_bio_candidates(
                    viewer, [item.profile_id for item in items], self._bio_tier_min_cos_sim, model
                )
                items = self._ranker.append_bio_tier(items, viewer, bio_candidates, now, same_offset_zone_ids)

        return FeedSession(viewer_profile_id=viewer.profile_id, params_hash=params_hash, items=items, built_at=now)

    async def _find_session(
        self, session_key: str, viewer: FeedViewer, params_hash: str
    ) -> FeedSession | None:
        session = await self._session_repository.get(session_key)
        # The key may come from the client's cursor, so a session is trusted only if it belongs to this viewer.
        if session is None or session.viewer_profile_id != viewer.profile_id or session.params_hash != params_hash:
            return None
        return session

    async def _hydrate(self, viewer: FeedViewer, ranked: list[FeedRankedItem]) -> list[FeedPageItem]:
        cards = await self._feed_repository.get_cards(viewer, [item.profile_id for item in ranked])
        card_by_id = {card.profile.id: card for card in cards}
        # A profile that was paused, banned or lost its username since the session was built drops out silently.
        return [
            FeedPageItem(
                card=card_by_id[item.profile_id],
                tier=item.tier,
                match_score=round(item.score * 100),
                reasons=item.reasons,
            )
            for item in ranked
            if item.profile_id in card_by_id
        ]

    def _track_page(
        self,
        user_id: int,
        viewer: FeedViewer,
        mode: FeedMode,
        filters: FeedFilters,
        session: FeedSession,
        is_reranked: bool,
        offset: int,
        page: FeedPage,
    ) -> None:
        common = {"actor_profile_id": viewer.profile_id, "mode": mode.value}
        if is_reranked:
            tiers = Counter(item.tier for item in session.items)
            self._analytics_tracker.track(
                AnalyticsEvent.FEED_RANKED,
                user_id,
                {
                    **common,
                    "filters_count": len(filters.criteria()),
                    "filter_role": filters.role_id is not None,
                    "filter_tags": bool(filters.tag_ids),
                    "filter_country": filters.country_code is not None,
                    "filter_timezone": filters.same_timezone,
                    # Attribute keys only, never the chosen values; sorted, so one combination is always one string.
                    "filter_attrs": ",".join(sorted(filters.attributes)) or None,
                    # In search the three tiers add up to the total; in discovery every item is the DISCOVERY tier.
                    "candidates_count": len(session.items),
                    "exact_count": tiers[FeedTier.EXACT_MATCH],
                    "partial_count": tiers[FeedTier.PARTIAL_MATCH],
                    "bio_count": tiers[FeedTier.RECOMMENDED_BY_BIO],
                },
            )
        self._analytics_tracker.track(
            AnalyticsEvent.FEED_PAGE_SERVED,
            user_id,
            {
                **common,
                "offset": offset,
                "items_count": len(page.items),
                "has_more": page.has_more,
                "is_reranked": is_reranked,
            },
        )
        # "Nobody yet" counts only when the feed was just ranked: reloading the same empty first page from a live
        # session must not double the cold-start signal. The end of the feed counts on every last page.
        if page.end_reason is FeedEndReason.END_OF_FEED or (
            page.end_reason is FeedEndReason.NO_CANDIDATES_YET and is_reranked
        ):
            self._analytics_tracker.track(
                AnalyticsEvent.FEED_EMPTY, user_id, {**common, "reason": page.end_reason.value}
            )

    async def execute(
        self,
        user: UserEntity,
        mode: FeedMode,
        filters: FeedFilters,
        cursor: FeedCursor | None,
        limit: int,
    ) -> FeedPage:
        profile, viewer = await self._viewer_resolver.resolve(user)

        if mode is FeedMode.SEARCH:
            filters = await self._filter_validator.validate(viewer.category_id, filters)
            if not filters.criteria():
                raise InvalidFeedFilterError("filters_required")
        else:
            filters = FeedFilters(role_id=None, attributes={}, tag_ids=[], country_code=None, same_timezone=False)

        params_hash = build_feed_params_hash(viewer.profile_id, mode, filters)
        if cursor is not None and cursor.params_hash != params_hash:
            raise InvalidFeedCursorError()

        current_key = build_feed_session_key(
            params_hash,
            profile.updated_at,
            viewer.has_comparable_vector,
            FEED_ALGORITHM_VERSION,
            self._scoring_fingerprint,
        )

        # A live session named by the cursor wins over the current key: a background event (the viewer's vector
        # arrived, a deploy) must not break scrolling that already started.
        session_key = current_key
        session = None
        if cursor is not None:
            session = await self._find_session(cursor.session_key, viewer, params_hash)
            if session is not None:
                session_key = cursor.session_key
        if session is None:
            session = await self._find_session(current_key, viewer, params_hash)
        is_reranked = session is None
        if session is None:
            # Only a miss is counted: building is the expensive part, and turning the pages of a live session is not.
            # It also covers an expired session named by a cursor, which is rebuilt here.
            await self._rate_limit_guard.check(RateLimitAction.FEED_SESSION_BUILD, user.id)
            session = await self._build_session(viewer, mode, filters, params_hash, datetime.now(timezone.utc))
            await self._session_repository.set(current_key, session)
        # On every page, so a profile view opened from this page finds the tier and the score its card had here.
        await self._session_repository.set_last_session_key(viewer.profile_id, session_key)

        offset = cursor.offset if cursor is not None else 0
        total = len(session.items)
        items = await self._hydrate(viewer, session.items[offset : offset + limit])

        if offset + limit < total:
            next_cursor = FeedCursor(offset=offset + limit, params_hash=params_hash, session_key=session_key)
            page = FeedPage(items=items, next_cursor=next_cursor, has_more=True, end_reason=None)
        else:
            # Empty from the very first page: nobody yet. Anything else is the honest end of what there is.
            end_reason = FeedEndReason.NO_CANDIDATES_YET if total == 0 and offset == 0 else FeedEndReason.END_OF_FEED
            page = FeedPage(items=items, next_cursor=None, has_more=False, end_reason=end_reason)
        self._track_page(user.id, viewer, mode, filters, session, is_reranked, offset, page)
        return page


class GetFeedCardUseCase:
    def __init__(
        self,
        viewer_resolver: FeedViewerResolver,
        feed_repository: IFeedRepository,
        scorer: FeedScorer,
        timezone_catalog: TimezoneCatalog,
        current_embedding_model: str,
    ) -> None:
        self._viewer_resolver = viewer_resolver
        self._feed_repository = feed_repository
        self._scorer = scorer
        self._timezone_catalog = timezone_catalog
        self._current_embedding_model = current_embedding_model

    async def execute(self, user: UserEntity, profile_id: int) -> FeedPageItem:
        _, viewer = await self._viewer_resolver.resolve(user)

        candidate = await self._feed_repository.get_candidate(viewer, profile_id, self._current_embedding_model)
        cards = await self._feed_repository.get_cards(viewer, [profile_id])
        if candidate is None or not cards:
            raise FeedProfileNotFoundError()

        now = datetime.now(timezone.utc)
        same_offset_zone_ids = self._timezone_catalog.zone_ids_with_same_offset(viewer.timezone, now)
        score, reasons = self._scorer.score(viewer, candidate, now, candidate.timezone in same_offset_zone_ids)
        # No tier: this card did not come out of a ranking.
        return FeedPageItem(card=cards[0], tier=None, match_score=round(score * 100), reasons=reasons)


class RecordProfileViewUseCase:
    def __init__(
        self,
        viewer_resolver: FeedViewerResolver,
        feed_repository: IFeedRepository,
        profile_view_repository: IProfileViewRepository,
        session_repository: IFeedSessionRepository,
        unit_of_work: IUnitOfWork,
        analytics_tracker: IAnalyticsTracker,
    ) -> None:
        self._viewer_resolver = viewer_resolver
        self._feed_repository = feed_repository
        self._profile_view_repository = profile_view_repository
        self._session_repository = session_repository
        self._unit_of_work = unit_of_work
        self._analytics_tracker = analytics_tracker

    async def _find_feed_item(self, viewer_profile_id: int, profile_id: int) -> FeedRankedItem | None:
        """The opened card as the viewer's last feed page ranked it, or None: no page was served lately, Redis is
        down, or the card was opened from somewhere else (a notification, `GET /feed/cards/{id}`)."""
        session_key = await self._session_repository.get_last_session_key(viewer_profile_id)
        if session_key is None:
            return None
        session = await self._session_repository.get(session_key)
        # Only the server writes the pointer, but a session is trusted only if it is the viewer's, as in the feed.
        if session is None or session.viewer_profile_id != viewer_profile_id:
            return None
        return next((item for item in session.items if item.profile_id == profile_id), None)

    async def execute(self, user: UserEntity, profile_id: int) -> None:
        _, viewer = await self._viewer_resolver.resolve(user)

        if not await self._feed_repository.get_cards(viewer, [profile_id]):
            raise FeedProfileNotFoundError()

        is_first_view = await self._profile_view_repository.add(
            viewer.profile_id, profile_id, datetime.now(timezone.utc)
        )
        await self._unit_of_work.commit()

        # After the commit, so a slow Redis never keeps the transaction open.
        item = await self._find_feed_item(viewer.profile_id, profile_id)
        self._analytics_tracker.track(
            AnalyticsEvent.PROFILE_VIEWED,
            user.id,
            {
                "actor_profile_id": viewer.profile_id,
                "target_profile_id": profile_id,
                # The same numbers the card showed: `match_score` is the card's 0..100.
                "match_type": item.tier.value if item is not None else None,
                "match_score": round(item.score * 100) if item is not None else None,
                "is_first_view": is_first_view,
            },
        )
