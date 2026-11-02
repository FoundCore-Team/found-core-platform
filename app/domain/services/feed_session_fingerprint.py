import hashlib
import json
from datetime import datetime

from app.domain.entities import FeedFilters
from app.domain.enums import FeedMode


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def build_feed_params_hash(viewer_profile_id: int, mode: FeedMode, filters: FeedFilters) -> str:
    """What the user chose: the viewer's active profile, the mode and the filters. Order of tags/attributes is ignored."""
    return _digest(
        {
            "viewer_profile_id": viewer_profile_id,
            "mode": mode.value,
            "role_id": filters.role_id,
            "attributes": sorted(filters.attributes.items()),
            "tag_ids": sorted(set(filters.tag_ids)),
            "country_code": filters.country_code,
            "same_timezone": filters.same_timezone,
        }
    )


def build_feed_session_key(
    params_hash: str,
    profile_updated_at: datetime,
    has_comparable_vector: bool,
    algorithm_version: int,
    scoring_fingerprint: str,
) -> str:
    """What changes by itself: any of these inputs makes the previous ranking stale, so the key changes with it."""
    return _digest(
        {
            "params_hash": params_hash,
            "profile_updated_at": profile_updated_at.isoformat(),
            "has_comparable_vector": has_comparable_vector,
            "algorithm_version": algorithm_version,
            "scoring_fingerprint": scoring_fingerprint,
        }
    )
