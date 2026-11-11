from enum import StrEnum


class RoleFieldType(StrEnum):
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    NUMBER = "number"
    TEXT = "text"
    BOOLEAN = "boolean"


class TagStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"
    # An alias of another tag (`merged_into_tag_id`): kept so old ids and titles still resolve to the target.
    MERGED = "merged"


class TagMergeTargetProblem(StrEnum):
    SAME_TAG = "same_tag"
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_NOT_APPROVED = "target_not_approved"
    TARGET_IS_ALIAS = "target_is_alias"
