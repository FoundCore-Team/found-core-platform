import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from app.core.config import ModerationConfig, Settings


def test_defaults_match_the_plan():
    config = ModerationConfig()

    assert (config.page_size_default, config.page_size_max, config.max_offset) == (50, 100, 100_000)


def test_moderation_config_is_a_plain_model_and_part_of_settings():
    # A BaseSettings default instance would read unprefixed environment variables.
    assert not issubclass(ModerationConfig, BaseSettings)
    assert "moderation" in Settings.model_fields


def test_a_default_page_size_equal_to_the_maximum_is_accepted():
    ModerationConfig(page_size_default=100, page_size_max=100)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(page_size_default=0),
        dict(page_size_default=-1),
        dict(page_size_default=101, page_size_max=100),
        dict(page_size_default=1, page_size_max=0),
        dict(max_offset=-1),
    ],
)
def test_inconsistent_values_are_refused(overrides):
    with pytest.raises(ValidationError):
        ModerationConfig(**overrides)
