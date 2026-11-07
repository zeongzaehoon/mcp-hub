"""S3 key layout (docs/S3_LAYOUT.md): Hive partitions under three roots, and ids that could escape a partition are refused."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from mcpcluster_common import layout


def test_keys_follow_the_contract():
    at = datetime(2026, 10, 1, 23, 30, tzinfo=timezone(timedelta(hours=9)))  # dt is the UTC day
    assert layout.artifact_key("co_a", "usr_b", "art_1") == "mcp/artifacts/tenant=co_a/user=usr_b/art_1.json"
    assert layout.file_key(layout.EXPORTS, "co_a", "usr_b", at, "f1", "x.csv") == "mcp/exports/tenant=co_a/user=usr_b/dt=20261001/f1/x.csv"
    assert layout.file_key(layout.REPORTS, "co_a", "usr_b", datetime(2026, 10, 2, tzinfo=UTC), "f1", "r.pdf").startswith("mcp/reports/tenant=co_a/user=usr_b/dt=20261002/")
    assert layout.monitor_prefix("postgres") == "control/monitors/server_type=postgres/"
    assert layout.monitor_key("postgres", "co_a", "usr_b", "mon_1") == "control/monitors/server_type=postgres/tenant=co_a/user=usr_b/mon_1.json"
    assert layout.monitor_state_key("co_a", "usr_b", "mon_1") == "control/monitor_state/tenant=co_a/user=usr_b/mon_1.json"
    for ds in (layout.ARTIFACTS, layout.EXPORTS, layout.REPORTS, layout.MONITORS, layout.MONITOR_STATE, layout.ACCESS_LOGS):
        assert ds.split("/")[0] in layout.ROOTS


def test_one_user_prefix_never_contains_another():
    # listing u1 must not pick up u10's objects: the partition ends with "/"
    assert not layout.owner_prefix(layout.ARTIFACTS, "co_a", "u10").startswith(layout.owner_prefix(layout.ARTIFACTS, "co_a", "u1"))


@pytest.mark.parametrize("bad", ["", "a/b", "a=b", "..", "../x", "a b", "é", None, 5, "x" * 65])
def test_ids_that_could_change_the_path_are_refused(bad):
    with pytest.raises(layout.LayoutError):
        layout.owner_prefix(layout.ARTIFACTS, bad, "u")
    with pytest.raises(layout.LayoutError):
        layout.owner_prefix(layout.ARTIFACTS, "c", bad)
    with pytest.raises(layout.LayoutError):
        layout.artifact_key("c", "u", bad)
    with pytest.raises(layout.LayoutError):
        layout.monitor_key(bad, "c", "u", "m")


@pytest.mark.parametrize("name", ["", ".x", "..", "a/b.csv", "a\\b.csv", "x" * 129])
def test_bad_file_names_and_datasets_are_refused(name):
    with pytest.raises(layout.LayoutError):
        layout.file_key(layout.EXPORTS, "c", "u", datetime.now(UTC), "f", name)
    with pytest.raises(layout.LayoutError):
        layout.file_key(layout.ARTIFACTS, "c", "u", datetime.now(UTC), "f", "x.csv")
