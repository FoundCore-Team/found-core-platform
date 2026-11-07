from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints

from app.domain.constants import ADMIN_REASON_MAX_LENGTH, MAX_INT64

Int64Id = Annotated[int, Field(ge=1, le=MAX_INT64)]


def _reject_nul(value: str) -> str:
    # Postgres refuses NUL in text, which would otherwise surface as a 500.
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


# Why an admin did something: trimmed, 1..ADMIN_REASON_MAX_LENGTH characters, no NUL.
AdminReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=ADMIN_REASON_MAX_LENGTH),
    AfterValidator(_reject_nul),
]
