from datetime import datetime, timedelta

from app.domain.entities import FeedCandidate, FeedReason, FeedViewer
from app.domain.enums import FeedReasonCode


class FeedScorer:
    """Applies the feed formula to one viewer-candidate pair.

    score = (w_sem * cos_sim + w_tags * tag_overlap + w_role * role_affinity) * activity_factor

    Without a comparable vector pair (`cos_sim is None`) the semantic weight is split between tags and role in the
    proportion of their own weights, so the feed keeps working, just less precisely.
    """

    def __init__(
        self,
        *,
        weight_semantic: float,
        weight_tags: float,
        weight_role: float,
        role_affinity_same_role: float,
        role_affinity_same_category: float,
        activity_recent_days: int,
        activity_stale_days: int,
        activity_factor_recent: float,
        activity_factor_stale: float,
        activity_factor_old: float,
        similar_bio_reason_min_cos_sim: float,
        max_reason_tags: int,
    ) -> None:
        self._weight_semantic = weight_semantic
        self._weight_tags = weight_tags
        self._weight_role = weight_role
        self._role_affinity_same_role = role_affinity_same_role
        self._role_affinity_same_category = role_affinity_same_category
        self._activity_recent = timedelta(days=activity_recent_days)
        self._activity_stale = timedelta(days=activity_stale_days)
        self._activity_factor_recent = activity_factor_recent
        self._activity_factor_stale = activity_factor_stale
        self._activity_factor_old = activity_factor_old
        self._similar_bio_min_cos_sim = similar_bio_reason_min_cos_sim
        self._max_reason_tags = max_reason_tags

    def _role_affinity(self, viewer: FeedViewer, candidate: FeedCandidate) -> float:
        if candidate.role_id == viewer.role_id:
            return self._role_affinity_same_role
        if candidate.category_id == viewer.category_id:
            return self._role_affinity_same_category
        return 0.0

    def _activity_factor(self, age: timedelta) -> float:
        if age <= self._activity_recent:
            return self._activity_factor_recent
        if age <= self._activity_stale:
            return self._activity_factor_stale
        return self._activity_factor_old

    def score(
        self,
        viewer: FeedViewer,
        candidate: FeedCandidate,
        now: datetime,
        same_utc_offset: bool,
    ) -> tuple[float, list[FeedReason]]:
        shared_tag_ids = sorted(set(viewer.tag_ids) & set(candidate.tag_ids))
        smaller_tag_set = min(len(set(viewer.tag_ids)), len(set(candidate.tag_ids)))
        tag_overlap = len(shared_tag_ids) / smaller_tag_set if smaller_tag_set else 0.0

        if candidate.cos_sim is None:
            weight_semantic = 0.0
            weight_tags = self._weight_tags + self._weight_semantic * self._weight_tags / (
                self._weight_tags + self._weight_role
            )
            weight_role = self._weight_role + self._weight_semantic * self._weight_role / (
                self._weight_tags + self._weight_role
            )
            cos_sim = 0.0
        else:
            weight_semantic = self._weight_semantic
            weight_tags = self._weight_tags
            weight_role = self._weight_role
            # SQL already clamps at 0; the upper bound guards against float noise like 1.0000001.
            cos_sim = min(1.0, max(0.0, candidate.cos_sim))

        age = now - candidate.last_active_at
        score = (
            weight_semantic * cos_sim
            + weight_tags * tag_overlap
            + weight_role * self._role_affinity(viewer, candidate)
        ) * self._activity_factor(age)

        reasons = []
        if shared_tag_ids:
            reasons.append(FeedReason(FeedReasonCode.SHARED_TAGS, tag_ids=shared_tag_ids[: self._max_reason_tags]))
        if candidate.role_id == viewer.role_id:
            reasons.append(FeedReason(FeedReasonCode.SAME_ROLE))
        if candidate.country_code == viewer.country_code:
            reasons.append(FeedReason(FeedReasonCode.SAME_COUNTRY))
        if same_utc_offset:
            reasons.append(FeedReason(FeedReasonCode.SAME_TIMEZONE))
        if candidate.cos_sim is not None and cos_sim >= self._similar_bio_min_cos_sim:
            reasons.append(FeedReason(FeedReasonCode.SIMILAR_BIO))
        if age <= self._activity_recent:
            reasons.append(FeedReason(FeedReasonCode.RECENTLY_ACTIVE))
        return score, reasons
