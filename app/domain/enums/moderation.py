from enum import StrEnum


class AdminActionType(StrEnum):
    """What an admin (or the `make_admin` script) did. One member per row type in the `admin_actions` journal."""

    BAN_USER = "ban_user"
    UNBAN_USER = "unban_user"
    HIDE_PROFILE = "hide_profile"
    UNHIDE_PROFILE = "unhide_profile"
    DISMISS_REPORTS = "dismiss_reports"
    APPROVE_TAG = "approve_tag"
    REJECT_TAG = "reject_tag"
    MERGE_TAG = "merge_tag"
    GRANT_ADMIN = "grant_admin"
    REVOKE_ADMIN = "revoke_admin"


class AdminTargetType(StrEnum):
    """What kind of row `admin_actions.target_id` points at. There is no foreign key: the journal outlives its target."""

    USER = "user"
    PROFILE = "profile"
    TAG = "tag"
