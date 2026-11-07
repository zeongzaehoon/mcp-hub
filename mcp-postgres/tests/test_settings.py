"""REDIS_PASSWORD merging and the non-dev startup guard (pure)."""

import pytest

from mcpcluster_common.settings import CommonSettings, redis_url_has_password, with_redis_password


@pytest.mark.parametrize(("url", "pw", "want"), [
    ("redis://redis:6379", "abc", "redis://:abc@redis:6379"),
    ("rediss://redis:6380/0", "abc", "rediss://:abc@redis:6380/0"),
    ("redis://worker@redis:6379", "abc", "redis://worker:abc@redis:6379"),  # ACL username kept
    ("redis://:set@redis:6379", "abc", "redis://:set@redis:6379"),  # explicit credentials win
    ("redis://redis:6379?password=set", "abc", "redis://redis:6379?password=set"),
    ("redis://redis:6379", "", "redis://redis:6379"),
    ("redis://redis:6379", "a/b@c", "redis://:a%2Fb%40c@redis:6379"),
])
def test_with_redis_password(url, pw, want):
    assert with_redis_password(url, pw) == want


def test_settings_merge_password():
    s = CommonSettings(redis_url="redis://redis:6379", redis_password="abc")
    assert s.redis_url == "redis://:abc@redis:6379"
    assert "redis_password" not in repr(s)


def test_non_dev_requires_redis_password():
    with pytest.raises(SystemExit, match="REDIS_PASSWORD"):
        CommonSettings(app_env="prod", redis_url="redis://redis:6379", redis_password="").ensure_safe_runtime()
    CommonSettings(app_env="prod", redis_url="rediss://redis:6379", redis_password="abc").ensure_safe_runtime()
    CommonSettings(app_env="dev", redis_url="redis://redis:6379", redis_password="").ensure_safe_runtime()


def test_unset_app_env_is_not_dev():
    with pytest.raises(SystemExit):
        CommonSettings(app_env="", redis_url="redis://redis:6379", redis_password="").ensure_safe_runtime()


def test_has_password():
    assert not redis_url_has_password("redis://worker@redis:6379")
    assert redis_url_has_password("redis://worker:x@redis:6379")
