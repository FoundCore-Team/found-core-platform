import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from app.core.config import FeedConfig, Settings


def test_defaults_match_the_documented_formula():
    config = FeedConfig()

    assert (config.weight_semantic, config.weight_tags, config.weight_role) == (0.4, 0.4, 0.2)
    assert (config.role_affinity_same_role, config.role_affinity_same_category) == (1.0, 0.5)
    assert (config.activity_recent_days, config.activity_stale_days) == (7, 30)
    assert (config.activity_factor_recent, config.activity_factor_stale, config.activity_factor_old) == (1.0, 0.8, 0.5)
    assert config.session_cap == 200
    assert config.session_ttl_seconds == 1800


def test_feed_config_is_a_plain_model_and_part_of_settings():
    # A BaseSettings default instance would read unprefixed environment variables.
    assert not issubclass(FeedConfig, BaseSettings)
    assert "feed" in Settings.model_fields


def test_weights_that_sum_to_one_only_in_floats_are_accepted():
    FeedConfig(weight_semantic=0.1, weight_tags=0.2, weight_role=0.7)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(weight_semantic=0.5),
        dict(weight_semantic=0.3, weight_tags=0.3, weight_role=0.3),
        dict(activity_recent_days=30, activity_stale_days=30),
        dict(activity_recent_days=0),
        dict(bio_tier_min_cos_sim=1.1),
        dict(bio_tier_min_cos_sim=-0.1),
        dict(similar_bio_reason_min_cos_sim=2),
        dict(page_size_default=0),
        dict(page_size_default=60, page_size_max=50),
        dict(session_cap=0),
        dict(max_cursor_offset=100),
    ],
)
def test_inconsistent_values_are_refused(overrides):
    with pytest.raises(ValidationError):
        FeedConfig(**overrides)


def test_scoring_fingerprint_is_stable():
    assert FeedConfig().scoring_fingerprint() == FeedConfig().scoring_fingerprint()
    assert len(FeedConfig().scoring_fingerprint()) == 64


@pytest.mark.parametrize(
    "overrides",
    [
        dict(weight_tags=0.3, weight_role=0.3),
        dict(role_affinity_same_category=0.4),
        dict(activity_recent_days=5),
        dict(activity_factor_old=0.4),
        dict(bio_tier_min_cos_sim=0.4),
        dict(similar_bio_reason_min_cos_sim=0.6),
        dict(max_reason_tags=3),
    ],
)
def test_changing_a_scoring_value_changes_the_fingerprint(overrides):
    assert FeedConfig(**overrides).scoring_fingerprint() != FeedConfig().scoring_fingerprint()


@pytest.mark.parametrize(
    "overrides",
    [
        dict(session_ttl_seconds=60),
        dict(session_cap=100),
        dict(page_size_default=10),
        dict(page_size_max=40),
        dict(max_cursor_offset=500),
    ],
)
def test_changing_a_non_scoring_value_keeps_the_fingerprint(overrides):
    assert FeedConfig(**overrides).scoring_fingerprint() == FeedConfig().scoring_fingerprint()
