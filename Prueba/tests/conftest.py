import random
from datetime import datetime, timezone

import pytest

from nubara_companion.adapters.mock_systems import MockSystems
from nubara_companion.clock import FixedClock
from nubara_companion.config import Settings
from nubara_companion.service import DisruptionService

NOW = datetime(2026, 9, 15, 11, 2, 35, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    return FixedClock(NOW)


@pytest.fixture
def systems(clock):
    return MockSystems(clock)


@pytest.fixture
def make_service(clock, systems):
    def _make(settings: Settings | None = None, composers=None):
        return DisruptionService(systems, clock, settings, composers, sleep=lambda s: None,
                                 rng=random.Random(1))
    return _make


@pytest.fixture
def service(make_service):
    return make_service()
