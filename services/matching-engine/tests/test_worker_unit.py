"""Worker input protection and configuration validation without database mutations."""

import math

import pytest

from matching_engine.worker import Worker, validate_embedding


@pytest.mark.parametrize("vector", [
    [], [1.0], [0.0] * 640,
    [math.nan] + [0.0] * 639,
    [math.inf] + [0.0] * 639,
    [2.0] + [0.0] * 639,
])
def test_invalid_or_degenerate_embedding_cannot_be_published(vector: list[float]) -> None:
    with pytest.raises(ValueError):
        validate_embedding(vector)


def test_normalized_embedding_is_accepted() -> None:
    validate_embedding([1.0] + [0.0] * 639)


@pytest.mark.parametrize("config", [
    {"lease_seconds": 0}, {"lease_seconds": 2},
    {"max_attempts": 0}, {"retry_base_seconds": -1},
])
def test_invalid_lease_or_retry_configuration_is_rejected(config: dict) -> None:
    with pytest.raises(ValueError):
        Worker(None, **config)
