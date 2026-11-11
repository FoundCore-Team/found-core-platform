from sqlalchemy.sql.expression import ColumnElement

from app.domain.enums import ProfileStatus
from app.infrastructure.models import ProfileModel, UserModel


def visible_to(viewer_user_id: int) -> list[ColumnElement[bool]]:
    """The single definition of "who may appear in somebody else's feed", "whose contact may be opened" and "whom
    one may report".

    These are SQL conditions over `ProfileModel` joined with `UserModel`, so they live in infrastructure; the feed, the
    contact repository and the report repository all import them, and they can never disagree. It takes a bare user id
    (not `FeedViewer`) because the id is all it uses, and a report does not need an active profile.
    """
    return [
        ProfileModel.status == ProfileStatus.ACTIVE,
        UserModel.is_banned.is_(False),
        UserModel.username.is_not(None),
        UserModel.username != "",
        UserModel.deleted_at.is_(None),
        # All of the viewer's own profiles, not only the active one.
        ProfileModel.user_id != viewer_user_id,
    ]
