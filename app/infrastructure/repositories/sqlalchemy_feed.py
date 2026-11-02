from sqlalchemy import BigInteger, Float, Text, and_, case, exists, func, null, or_, select, true
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import CTE, ColumnElement, Select

from app.domain.entities import FeedCandidate, FeedCard, FeedFilters, FeedViewer, TagEntity
from app.domain.enums import ProfileStatus, TagStatus
from app.domain.interfaces import IFeedRepository
from app.infrastructure.mappers.feed_mapper import map_feed_candidate_row_to_feed_candidate_entity
from app.infrastructure.mappers.profile_mapper import map_profile_model_to_profile_entity
from app.infrastructure.mappers.taxonomy_mapper import map_tag_model_to_tag_entity
from app.infrastructure.models import (
    ProfileEmbeddingModel,
    ProfileModel,
    ProfileTagModel,
    ProfileViewModel,
    TagModel,
    UserModel,
)

# The feed is a read model: Postgres selects the candidates and computes the signals (cosine similarity, tags, which
# search criteria matched); the formula that turns them into a score lives in `FeedScorer`. Vectors never leave
# Postgres: `<=>` compares two columns and only the resulting number travels to Python. That is why the "no
# register_vector" rule (CLAUDE.md, "Tech stack") does not get in the way: it is about the asyncpg codec for vectors
# that cross the driver.

# `matched_filters` of the queries that have no search criteria (discovery, bio tier, one card). Postgres cannot
# infer the type of a bare `ARRAY[]`, so the element type is given and it renders as `ARRAY[]::TEXT[]`.
_NO_MATCHED_FILTERS = array([], type_=Text)


def _visible_to(viewer: FeedViewer) -> list[ColumnElement[bool]]:
    """The single definition of "who may appear in somebody else's feed"."""
    return [
        ProfileModel.status == ProfileStatus.ACTIVE,
        UserModel.is_banned.is_(False),
        UserModel.username.is_not(None),
        UserModel.username != "",
        UserModel.deleted_at.is_(None),
        # All of the viewer's own profiles, not only the active one.
        ProfileModel.user_id != viewer.user_id,
    ]


def _viewer_vector(viewer: FeedViewer, embedding_model: str) -> CTE:
    """The viewer's vector of the current model: a CTE with one row, or with none if there is no such vector.

    It is LEFT JOINed to every candidate, so without a row `viewer.embedding` is NULL for everybody and so is every
    cosine similarity: the semantic term of the score is then skipped instead of the query failing.
    """
    return (
        select(ProfileEmbeddingModel.embedding)
        .where(
            ProfileEmbeddingModel.profile_id == viewer.profile_id,
            ProfileEmbeddingModel.embedding.is_not(None),
            ProfileEmbeddingModel.model == embedding_model,
        )
        .cte("viewer")
    )


def _select_candidates(
    viewer: FeedViewer,
    viewer_vector: CTE,
    embedding_model: str,
    matched_filters: ColumnElement,
) -> Select:
    """The signals of every profile the viewer may see; the callers add their own conditions and the order."""
    # `<=>` is the cosine distance (0 = same direction), so the similarity is `1 - distance`. A negative similarity is
    # clamped to 0 so that it cannot pull the score below what tags and role give.
    distance = ProfileEmbeddingModel.embedding.cosine_distance(viewer_vector.c.embedding)
    cos_sim = case(
        (
            # NULL, not 0, unless both sides have a vector and the candidate's is of the current model (vectors of
            # two models are not comparable). `GREATEST` ignores NULLs, so without this guard a missing vector
            # would become a similarity of 0.
            and_(
                viewer_vector.c.embedding.is_not(None),
                ProfileEmbeddingModel.embedding.is_not(None),
                ProfileEmbeddingModel.model == embedding_model,
            ),
            func.greatest(0, 1 - distance, type_=Float),
        )
    )

    # Ids of the profile's tags without the rejected ones, ordered; `{}` (not NULL) for a profile without tags.
    tag_ids_of_profile = (
        select(ProfileTagModel.tag_id)
        .join(TagModel, TagModel.id == ProfileTagModel.tag_id)
        .where(ProfileTagModel.profile_id == ProfileModel.id, TagModel.status != TagStatus.REJECTED)
        .order_by(ProfileTagModel.tag_id)
        .scalar_subquery()
    )
    tag_ids = func.array(tag_ids_of_profile, type_=ARRAY(BigInteger))

    return (
        select(
            ProfileModel.id.label("profile_id"),
            ProfileModel.category_id,
            ProfileModel.role_id,
            ProfileModel.country_code,
            ProfileModel.timezone,
            UserModel.last_active_at,
            cos_sim.label("cos_sim"),
            tag_ids.label("tag_ids"),
            matched_filters.label("matched_filters"),
        )
        .select_from(ProfileModel)
        .join(UserModel, UserModel.id == ProfileModel.user_id)
        .outerjoin(ProfileEmbeddingModel, ProfileEmbeddingModel.profile_id == ProfileModel.id)
        .outerjoin(viewer_vector, true())
        .where(*_visible_to(viewer))
    )


class SqlAlchemyFeedRepository(IFeedRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _fetch(self, stmt: Select) -> list[FeedCandidate]:
        result = await self._session.execute(stmt)
        return [map_feed_candidate_row_to_feed_candidate_entity(row) for row in result.all()]

    async def list_discovery_candidates(self, viewer: FeedViewer, embedding_model: str) -> list[FeedCandidate]:
        viewer_vector = _viewer_vector(viewer, embedding_model)
        stmt = (
            _select_candidates(viewer, viewer_vector, embedding_model, _NO_MATCHED_FILTERS)
            .where(ProfileModel.category_id == viewer.category_id)
            .order_by(ProfileModel.id)
        )
        return await self._fetch(stmt)

    async def list_search_candidates(
        self,
        viewer: FeedViewer,
        filters: FeedFilters,
        timezone_ids: list[str],
        embedding_model: str,
    ) -> list[FeedCandidate]:
        # One (code, condition) pair per criterion the viewer set. The order is the one of `FeedFilters.criteria()`:
        # it is also the order of the codes in `matched_filters`.
        criteria: list[tuple[str, ColumnElement[bool]]] = []

        if filters.role_id is not None:
            criteria.append(("role", ProfileModel.role_id == filters.role_id))

        # Every attribute is its own criterion. `@>` ("contains") is true for an equal value, false for an unset one.
        for key in sorted(filters.attributes):
            criteria.append((f"attr:{key}", ProfileModel.extra_attributes.contains({key: filters.attributes[key]})))

        # All the selected tags are one criterion: the profile has at least one of them.
        if filters.tag_ids:
            has_selected_tag = (
                select(ProfileTagModel.profile_id)
                .where(ProfileTagModel.profile_id == ProfileModel.id, ProfileTagModel.tag_id.in_(filters.tag_ids))
                .exists()
            )
            criteria.append(("tags", has_selected_tag))

        if filters.country_code is not None:
            criteria.append(("country", ProfileModel.country_code == filters.country_code))

        if filters.same_timezone:
            criteria.append(("timezone", ProfileModel.timezone.in_(timezone_ids)))

        # A search without a criterion has nothing to match: nobody, not everybody.
        if not criteria:
            return []

        # `CASE` gives the code of a criterion the profile matches and NULL otherwise; `array_remove` drops the NULLs,
        # so what is left is the codes of the matched criteria.
        code_or_null = [case((condition, code)) for code, condition in criteria]
        matched_filters = func.array_remove(array(code_or_null, type_=Text), null(), type_=ARRAY(Text))
        at_least_one_criterion_matches = or_(*[condition for _, condition in criteria])

        viewer_vector = _viewer_vector(viewer, embedding_model)
        stmt = (
            _select_candidates(viewer, viewer_vector, embedding_model, matched_filters)
            .where(ProfileModel.category_id == viewer.category_id, at_least_one_criterion_matches)
            .order_by(ProfileModel.id)
        )
        return await self._fetch(stmt)

    async def list_bio_candidates(
        self,
        viewer: FeedViewer,
        exclude_profile_ids: list[int],
        min_cos_sim: float,
        embedding_model: str,
    ) -> list[FeedCandidate]:
        viewer_vector = _viewer_vector(viewer, embedding_model)

        # The same similarity as `cos_sim` in `_select_candidates`, now used as a condition. The guard is spelled out
        # again on purpose: with a threshold of 0 a profile without a vector must not pass.
        both_have_current_vectors = and_(
            viewer_vector.c.embedding.is_not(None),
            ProfileEmbeddingModel.embedding.is_not(None),
            ProfileEmbeddingModel.model == embedding_model,
        )
        distance = ProfileEmbeddingModel.embedding.cosine_distance(viewer_vector.c.embedding)
        cos_sim = func.greatest(0, 1 - distance, type_=Float)

        # No category condition: this tier looks across all categories.
        stmt = (
            _select_candidates(viewer, viewer_vector, embedding_model, _NO_MATCHED_FILTERS)
            .where(
                both_have_current_vectors,
                cos_sim >= min_cos_sim,
                ProfileModel.id.not_in(exclude_profile_ids),
            )
            .order_by(ProfileModel.id)
        )
        return await self._fetch(stmt)

    async def get_candidate(self, viewer: FeedViewer, profile_id: int, embedding_model: str) -> FeedCandidate | None:
        viewer_vector = _viewer_vector(viewer, embedding_model)
        stmt = _select_candidates(viewer, viewer_vector, embedding_model, _NO_MATCHED_FILTERS).where(
            ProfileModel.id == profile_id
        )
        candidates = await self._fetch(stmt)
        return candidates[0] if candidates else None

    async def get_cards(self, viewer: FeedViewer, profile_ids: list[int]) -> list[FeedCard]:
        if not profile_ids:
            return []

        is_viewed = exists().where(
            ProfileViewModel.viewer_profile_id == viewer.profile_id,
            ProfileViewModel.viewed_profile_id == ProfileModel.id,
        )
        # Visibility is checked again: a profile may have been paused or banned since the session was built.
        rows = (
            await self._session.execute(
                select(ProfileModel, UserModel.first_name, UserModel.photo_url, is_viewed.label("is_viewed"))
                .join(UserModel, UserModel.id == ProfileModel.user_id)
                .where(ProfileModel.id.in_(profile_ids), *_visible_to(viewer))
            )
        ).all()
        if not rows:
            return []

        visible_ids = [model.id for model, _, _, _ in rows]
        tags: dict[int, list[TagEntity]] = {profile_id: [] for profile_id in visible_ids}
        tag_rows = await self._session.execute(
            select(ProfileTagModel.profile_id, TagModel)
            .join(TagModel, TagModel.id == ProfileTagModel.tag_id)
            .where(ProfileTagModel.profile_id.in_(visible_ids), TagModel.status != TagStatus.REJECTED)
            .order_by(ProfileTagModel.profile_id, TagModel.id)
        )
        for profile_id, tag_model in tag_rows.all():
            tags[profile_id].append(map_tag_model_to_tag_entity(tag_model))

        cards = {
            model.id: FeedCard(
                profile=map_profile_model_to_profile_entity(model, tags[model.id]),
                first_name=first_name,
                photo_url=photo_url,
                is_viewed=viewed,
            )
            for model, first_name, photo_url, viewed in rows
        }
        # The order of the request, not of the database; what is not visible is simply absent.
        return [cards[profile_id] for profile_id in profile_ids if profile_id in cards]


