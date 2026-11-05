from sqlalchemy.sql.expression import ColumnElement

from app.domain.entities import FeedViewer
from app.domain.enums import ProfileStatus
from app.infrastructure.models import ProfileModel, UserModel


def visible_to(viewer: FeedViewer) -> list[ColumnElement[bool]]:
    """The single definition of "who may appear in somebody else's feed" and "whose contact may be opened".

    These are SQL conditions over `ProfileModel` joined with `UserModel`, so they live in infrastructure; the feed and
    the contact repository both import them, and the two can never disagree.
    """
    return [
        ProfileModel.status == ProfileStatus.ACTIVE,
        UserModel.is_banned.is_(False),
        UserModel.username.is_not(None),
        UserModel.username != "",
        UserModel.deleted_at.is_(None),
        # All of the viewer's own profiles, not only the active one.
        ProfileModel.user_id != viewer.user_id,
    ]
