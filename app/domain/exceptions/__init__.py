__all__ = [
    "InitDataMalformedError",
    "InitDataSignatureInvalidError",
    "InitDataExpiredError",
    "TokenExpiredError",
    "TokenInvalidError",
    "UserBannedError",
    "CategoryNotFoundError",
    "RoleNotFoundError",
    "TagTitleInvalidError",
    "TagRejectedError",
    "ProfileNotFoundError",
    "ProfileAlreadyExistsError",
    "ProfileNotActivatableError",
    "ProfileHiddenError",
    "InvalidProfileTextError",
    "InvalidCountryError",
    "InvalidTimezoneError",
    "TooFewTagsError",
    "TooManyTagsError",
    "ProfileTagInvalidError",
    "InvalidExtraAttributesError",
    "EmbeddingProviderUnavailableError",
    "EmbeddingRejectedError",
    "ProfileChangedDuringEmbeddingError",
    "NoActiveProfileError",
    "UsernameRequiredError",
    "InvalidFeedFilterError",
    "InvalidFeedCursorError",
    "FeedProfileNotFoundError",
]

from app.domain.exceptions.auth import (
    InitDataExpiredError,
    InitDataMalformedError,
    InitDataSignatureInvalidError,
    TokenExpiredError,
    TokenInvalidError,
)
from app.domain.exceptions.embedding import (
    EmbeddingProviderUnavailableError,
    EmbeddingRejectedError,
    ProfileChangedDuringEmbeddingError,
)
from app.domain.exceptions.feed import (
    FeedProfileNotFoundError,
    InvalidFeedCursorError,
    InvalidFeedFilterError,
    NoActiveProfileError,
    UsernameRequiredError,
)
from app.domain.exceptions.profile import (
    InvalidCountryError,
    InvalidExtraAttributesError,
    InvalidProfileTextError,
    InvalidTimezoneError,
    ProfileAlreadyExistsError,
    ProfileHiddenError,
    ProfileNotActivatableError,
    ProfileNotFoundError,
    ProfileTagInvalidError,
    TooFewTagsError,
    TooManyTagsError,
)
from app.domain.exceptions.taxonomy import (
    CategoryNotFoundError,
    RoleNotFoundError,
    TagRejectedError,
    TagTitleInvalidError,
)
from app.domain.exceptions.user import UserBannedError
