"""Non-dev startup guard and REDIS_PASSWORD merging (pure; no Dynamo/Redis)."""

from app.core.config import DEV_JWT_SECRET, DEV_MASTER_PASSWORD, Settings

STRONG = {"jwt_secret": "x" * 32, "master_password": "y" * 12, "redis_url": "redis://redis:6379", "redis_password": "abc",
          "google_client_id": "", "google_client_secret": "", "google_issuer": "https://accounts.google.com",
          "google_directory_url": "https://admin.googleapis.com", "google_token_url": "https://oauth2.googleapis.com/token"}


def _s(**over) -> Settings:
    return Settings(_env_file=None, **{"app_env": "prod", **STRONG, **over})


def test_strong_non_dev_config_passes():
    s = _s()
    assert s.runtime_problems() == []
    assert s.redis_url == "redis://:abc@redis:6379"


def test_dev_defaults_rejected_outside_dev():
    problems = _s(jwt_secret=DEV_JWT_SECRET, master_password=DEV_MASTER_PASSWORD, redis_password="").runtime_problems()
    assert len(problems) == 3
    assert any("JWT_SECRET" in p for p in problems)
    assert any("MASTER_PASSWORD" in p for p in problems)
    assert any("REDIS_PASSWORD" in p for p in problems)


def test_short_values_rejected():
    assert any("JWT_SECRET" in p for p in _s(jwt_secret="x" * 31).runtime_problems())
    assert any("MASTER_PASSWORD" in p for p in _s(master_password="y" * 11).runtime_problems())


def test_unset_app_env_is_not_dev():
    assert _s(app_env="", jwt_secret=DEV_JWT_SECRET).runtime_problems()


def test_dev_allows_defaults():
    assert _s(app_env="dev", jwt_secret=DEV_JWT_SECRET, master_password=DEV_MASTER_PASSWORD, redis_password="").runtime_problems() == []


def test_credentials_in_url_are_kept():
    assert _s(redis_url="redis://:set@redis:6379").redis_url == "redis://:set@redis:6379"


def test_platform_google_guard_outside_dev():
    ok = _s(google_client_id="cid", google_client_secret="sec")
    assert ok.runtime_problems() == [] and ok.google_enabled
    assert any("GOOGLE_CLIENT_SECRET" in p for p in _s(google_client_id="cid").runtime_problems())
    assert any("https" in p for p in _s(google_client_id="cid", google_client_secret="s", google_issuer="http://mock-idp:8080/google").runtime_problems())
    # the directory sync's token/API URLs must be https outside dev even without the platform client
    assert any("GOOGLE_TOKEN_URL" in p for p in _s(google_token_url="http://mock-idp:8080/google/token").runtime_problems())
    assert _s(app_env="dev", google_client_id="cid", google_client_secret="s", google_issuer="http://mock-idp:8080/google", google_token_url="http://mock-idp:8080/google/token").runtime_problems() == []
