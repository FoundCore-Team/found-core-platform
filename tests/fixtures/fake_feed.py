from copy import deepcopy
from dataclasses import replace
from datetime import datetime

from app.domain.entities import FeedCandidate, FeedCard, FeedFilters, FeedSession, FeedViewer
from app.domain.interfaces import IFeedRepository, IFeedSessionRepository, IProfileViewRepository


class FakeFeedRepository(IFeedRepository):
    """In-memory stand-in for SqlAlchemyFeedRepository. Implements the real ABC and returns copies.

    The candidate lists are preset by the test; the fake does not evaluate filters, so it only checks what the use
    case asks (`search_args`, `bio_args`). Visibility is modelled by `cards`: `get_cards` returns only the profiles
    present there, in the order asked, and `remove_card` plays a profile that was paused or banned meanwhile.
    """

    def __init__(
        self,
        *,
        discovery: list[FeedCandidate] | None = None,
        search: list[FeedCandidate] | None = None,
        bio: list[FeedCandidate] | None = None,
        candidates: list[FeedCandidate] | None = None,
        cards: list[FeedCard] | None = None,
        viewed: set[tuple[int, int]] | None = None,
    ) -> None:
        self.discovery = list(discovery or [])
        self.search = list(search or [])
        self.bio = list(bio or [])
        self.extra_candidates = list(candidates or [])
        self._cards: dict[int, FeedCard] = {card.profile.id: card for card in (cards or [])}
        self.viewed: set[tuple[int, int]] = set(viewed or ())
        self.calls: list[str] = []
        self.search_args: list[dict] = []
        self.bio_args: list[dict] = []

    def remove_card(self, profile_id: int) -> None:
        del self._cards[profile_id]

    async def list_discovery_candidates(self, viewer: FeedViewer, embedding_model: str) -> list[FeedCandidate]:
        self.calls.append("discovery")
        return [replace(c) for c in self.discovery]

    async def list_search_candidates(
        self,
        viewer: FeedViewer,
        filters: FeedFilters,
        timezone_ids: list[str],
        embedding_model: str,
    ) -> list[FeedCandidate]:
        self.calls.append("search")
        self.search_args.append({"filters": filters, "timezone_ids": list(timezone_ids)})
        return [replace(c) for c in self.search]

    async def list_bio_candidates(
        self,
        viewer: FeedViewer,
        exclude_profile_ids: list[int],
        min_cos_sim: float,
        embedding_model: str,
    ) -> list[FeedCandidate]:
        self.calls.append("bio")
        self.bio_args.append({"exclude": list(exclude_profile_ids), "min_cos_sim": min_cos_sim})
        return [replace(c) for c in self.bio]

    async def get_candidate(self, viewer: FeedViewer, profile_id: int, embedding_model: str) -> FeedCandidate | None:
        self.calls.append("candidate")
        pool = [*self.discovery, *self.search, *self.bio, *self.extra_candidates]
        found = next((c for c in pool if c.profile_id == profile_id), None)
        return replace(found) if found else None

    async def get_cards(self, viewer: FeedViewer, profile_ids: list[int]) -> list[FeedCard]:
        self.calls.append("cards")
        return [
            replace(
                self._cards[pid],
                profile=replace(self._cards[pid].profile),
                is_viewed=(viewer.profile_id, pid) in self.viewed,
            )
            for pid in profile_ids
            if pid in self._cards
        ]


class FakeFeedSessionRepository(IFeedSessionRepository):
    """Dict-backed session store. With `outage=True` it behaves like the Redis one during an outage: reads return
    None and writes are no-ops. `expire()` drops everything, like the TTL does (sessions and pointers alike)."""

    def __init__(self, outage: bool = False) -> None:
        self.outage = outage
        self._store: dict[str, FeedSession] = {}
        self.last_session_keys: dict[int, str] = {}
        self.gets: list[str] = []
        self.sets: list[str] = []

    def expire(self) -> None:
        self._store.clear()
        self.last_session_keys.clear()

    def put(self, session_key: str, session: FeedSession) -> None:
        self._store[session_key] = deepcopy(session)

    @property
    def keys(self) -> list[str]:
        return list(self._store)

    async def get(self, session_key: str) -> FeedSession | None:
        self.gets.append(session_key)
        if self.outage:
            return None
        session = self._store.get(session_key)
        return deepcopy(session) if session is not None else None

    async def set(self, session_key: str, session: FeedSession) -> None:
        self.sets.append(session_key)
        if not self.outage:
            self._store[session_key] = deepcopy(session)

    async def get_last_session_key(self, viewer_profile_id: int) -> str | None:
        if self.outage:
            return None
        return self.last_session_keys.get(viewer_profile_id)

    async def set_last_session_key(self, viewer_profile_id: int, session_key: str) -> None:
        if not self.outage:
            self.last_session_keys[viewer_profile_id] = session_key


class FakeProfileViewRepository(IProfileViewRepository):
    """A set of (viewer, viewed) pairs: a repeat `add` changes nothing, like `ON CONFLICT DO NOTHING`."""

    def __init__(self) -> None:
        self.views: dict[tuple[int, int], datetime] = {}
        self.add_calls = 0

    async def add(self, viewer_profile_id: int, viewed_profile_id: int, viewed_at: datetime) -> bool:
        self.add_calls += 1
        if (viewer_profile_id, viewed_profile_id) in self.views:
            return False
        self.views[(viewer_profile_id, viewed_profile_id)] = viewed_at
        return True
