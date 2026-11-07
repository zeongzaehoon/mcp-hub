"""RateLimiter unit test against the local redis (skips if unreachable)."""

import os
import uuid

import pytest
import redis

from mcpcluster_common.ratelimit import RateLimiter
from mcpcluster_common.settings import CommonSettings

URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379")
try:
    redis.from_url(URL).ping()
except Exception:  # noqa: BLE001
    pytest.skip("redis not reachable", allow_module_level=True)


@pytest.fixture
def limiter():
    s = CommonSettings(redis_url=URL, redis_db_hot=14, rate_limit_default_per_minute=3)
    return RateLimiter(s)


async def test_explicit_limit_enforced(limiter):
    kid = f"k{uuid.uuid4().hex[:6]}"
    results = [await limiter.check(kid, 2) for _ in range(3)]
    assert [r.allowed for r in results] == [True, True, False]
    assert results[1].remaining == 0 and results[2].retry_after_s >= 1


async def test_default_limit_and_disable(limiter):
    kid = f"k{uuid.uuid4().hex[:6]}"
    assert [(await limiter.check(kid, None)).allowed for _ in range(4)] == [True, True, True, False]
    assert (await limiter.check(kid, 0)).allowed  # 0 disables
