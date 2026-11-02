from sqlalchemy import Row

from app.domain.entities import FeedCandidate


def map_feed_candidate_row_to_feed_candidate_entity(row: Row) -> FeedCandidate:
    return FeedCandidate(
        profile_id=row.profile_id,
        category_id=row.category_id,
        role_id=row.role_id,
        country_code=row.country_code,
        timezone=row.timezone,
        tag_ids=list(row.tag_ids),
        cos_sim=row.cos_sim,
        last_active_at=row.last_active_at,
        matched_filters=list(row.matched_filters),
    )
