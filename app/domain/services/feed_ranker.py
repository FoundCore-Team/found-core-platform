from collections.abc import Collection
from datetime import datetime

from app.domain.entities import FeedCandidate, FeedFilters, FeedRankedItem, FeedReason, FeedViewer
from app.domain.enums import FeedReasonCode, FeedTier
from app.domain.services.feed_scorer import FeedScorer


class FeedRanker:
    """Turns candidates into an ordered, capped list of ranked items.

    Search is a cascade: exact matches first, then partial ones (ordered by how many filter criteria they matched, then
    by score), then the bio tier. Every candidate lands in exactly one tier.
    """

    def __init__(self, scorer: FeedScorer, session_cap: int) -> None:
        self._scorer = scorer
        self._session_cap = session_cap

    def _item(
        self,
        viewer: FeedViewer,
        candidate: FeedCandidate,
        tier: FeedTier,
        now: datetime,
        same_offset_zone_ids: Collection[str],
    ) -> FeedRankedItem:
        score, reasons = self._scorer.score(viewer, candidate, now, candidate.timezone in same_offset_zone_ids)
        return FeedRankedItem(profile_id=candidate.profile_id, tier=tier, score=score, reasons=reasons)

    def rank_discovery(
        self,
        viewer: FeedViewer,
        candidates: list[FeedCandidate],
        now: datetime,
        same_offset_zone_ids: Collection[str],
    ) -> list[FeedRankedItem]:
        items = [self._item(viewer, c, FeedTier.DISCOVERY, now, same_offset_zone_ids) for c in candidates]
        items.sort(key=lambda item: (-item.score, item.profile_id))
        return items[: self._session_cap]

    def rank_search(
        self,
        viewer: FeedViewer,
        filters: FeedFilters,
        candidates: list[FeedCandidate],
        now: datetime,
        same_offset_zone_ids: Collection[str],
    ) -> list[FeedRankedItem]:
        """Tier 1 (all criteria matched) followed by tier 2 (some matched), cut to the session cap."""
        criteria = filters.criteria()
        exact: list[tuple[int, FeedRankedItem]] = []
        partial: list[tuple[int, FeedRankedItem]] = []
        for candidate in candidates:
            matched = [code for code in criteria if code in candidate.matched_filters]
            if not matched:
                continue
            is_exact = len(matched) == len(criteria)
            tier = FeedTier.EXACT_MATCH if is_exact else FeedTier.PARTIAL_MATCH
            item = self._item(viewer, candidate, tier, now, same_offset_zone_ids)
            missed = [code for code in criteria if code not in matched]
            item.reasons.insert(0, FeedReason(FeedReasonCode.MATCHED_FILTERS, matched=matched, missed=missed))
            (exact if is_exact else partial).append((len(matched), item))

        exact.sort(key=lambda pair: (-pair[1].score, pair[1].profile_id))
        partial.sort(key=lambda pair: (-pair[0], -pair[1].score, pair[1].profile_id))
        return [item for _, item in exact + partial][: self._session_cap]

    def needs_bio_tier(self, ranked: list[FeedRankedItem]) -> bool:
        return len(ranked) < self._session_cap

    def append_bio_tier(
        self,
        ranked: list[FeedRankedItem],
        viewer: FeedViewer,
        bio_candidates: list[FeedCandidate],
        now: datetime,
        same_offset_zone_ids: Collection[str],
    ) -> list[FeedRankedItem]:
        """Adds tier 3 after the given items. The cosine threshold is applied by the repository query."""
        seen = {item.profile_id for item in ranked}
        bio_items = [
            self._item(viewer, c, FeedTier.RECOMMENDED_BY_BIO, now, same_offset_zone_ids)
            for c in bio_candidates
            if c.profile_id not in seen
        ]
        bio_items.sort(key=lambda item: (-item.score, item.profile_id))
        return (ranked + bio_items)[: self._session_cap]
