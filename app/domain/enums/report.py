from enum import StrEnum


class ReportReason(StrEnum):
    """Why a user reports a profile. Chosen from a fixed list; free text goes into the optional comment."""

    SPAM = "spam"
    FAKE = "fake"
    OFFENSIVE = "offensive"
    INAPPROPRIATE_CONTACT = "inappropriate_contact"
    OTHER = "other"


class ReportStatus(StrEnum):
    """`open` waits for an admin; the other two are final and keep the report for history.

    `resolved`: the admin acted (hid the profile, banned the owner). `dismissed`: the admin found no violation.
    """

    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
