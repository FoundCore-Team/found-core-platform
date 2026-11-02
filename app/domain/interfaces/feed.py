from abc import ABC, abstractmethod

from app.domain.entities import FeedCandidate, FeedCard, FeedFilters, FeedViewer


class IFeedRepository(ABC):
    """Read model of the feed: candidates with their raw signals. Never writes and never commits.

    A candidate is visible only if its profile is active, its user is not banned, has a non-empty username and is not
    deleted, and it is not one of the viewer's own profiles. A vector is compared only if both sides have one of
    `embedding_model`; otherwise `cos_sim` is None. Rejected tags are never part of `tag_ids`. Every list is ordered
    by profile id; the ranking itself happens in the domain.
    """

    @abstractmethod
    async def list_discovery_candidates(self, viewer: FeedViewer, embedding_model: str) -> list[FeedCandidate]:
        """Every visible profile of the viewer's category."""

    @abstractmethod
    async def list_search_candidates(
        self,
        viewer: FeedViewer,
        filters: FeedFilters,
        timezone_ids: list[str],
        embedding_model: str,
    ) -> list[FeedCandidate]:
        """Visible profiles of the viewer's category that match at least one criterion of `filters`.

        `matched_filters` lists the matched criteria (see FeedFilters.criteria). `timezone_ids` are the zones that
        count as "the viewer's timezone"; it is only read when `filters.same_timezone` is set.
        """

    @abstractmethod
    async def list_bio_candidates(
        self,
        viewer: FeedViewer,
        exclude_profile_ids: list[int],
        min_cos_sim: float,
        embedding_model: str,
    ) -> list[FeedCandidate]:
        """Visible profiles of any category with a comparable vector and `cos_sim >= min_cos_sim`."""

    @abstractmethod
    async def get_candidate(self, viewer: FeedViewer, profile_id: int, embedding_model: str) -> FeedCandidate | None:
        """One visible profile of any category, or None if it does not exist or is not visible to the viewer."""

    @abstractmethod
    async def get_cards(self, viewer: FeedViewer, profile_ids: list[int]) -> list[FeedCard]:
        """Cards of the visible profiles among `profile_ids`, in the order of `profile_ids`.

        Profiles that do not exist or stopped being visible are simply absent. Rejected tags are left out of the card;
        `is_viewed` is true if the viewer's active profile has opened that profile.
        """
