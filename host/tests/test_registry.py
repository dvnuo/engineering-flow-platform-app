from __future__ import annotations

import pytest

from efp_app_host.config import Settings, SettingsError
from efp_app_host.models import AppRecord, Release, is_valid_slug
from efp_app_host.registry import AppNotFound, Registry, SlugTaken


def test_upsert_assigns_uid_and_port_once(settings, registry):
    first, created = registry.upsert("a1", slug="mindmap", name="Mind map", visibility="public")
    second, _ = registry.upsert("a2", slug="review", name="Review")
    assert created is True
    assert (first.uid, first.port) == (settings.uid_range_start, settings.port_range_start)
    assert (second.uid, second.port) == (settings.uid_range_start + 1, settings.port_range_start + 1)

    again, created_again = registry.upsert("a1", slug="mindmap", name="Renamed")
    assert created_again is False
    assert again.name == "Renamed"
    assert (again.uid, again.port) == (first.uid, first.port)


def test_slug_belongs_to_one_app(registry):
    registry.upsert("a1", slug="mindmap")
    with pytest.raises(SlugTaken):
        registry.upsert("a2", slug="mindmap")


def test_registry_survives_a_reload(settings, registry):
    registry.upsert("a1", slug="mindmap", visitor_key="k", enabled=False, disabled_reason="taken down")
    registry.add_release("a1", Release(sha="s1", deployed_at="2026-11-15T00:00:00Z", type="fastapi", manifest={"entry": "main:app"}))
    registry.revoke_user(7, at=100.0)

    reloaded = Registry(settings)
    record = reloaded.get("a1")
    assert isinstance(record, AppRecord)
    assert record.enabled is False and record.disabled_reason == "taken down"
    assert record.current is not None and record.current.manifest == {"entry": "main:app"}
    assert record.type == "fastapi"
    assert reloaded.revoked_before(7) == 100.0
    assert reloaded.revoked_before(8) == 0.0


def test_releases_keep_current_and_previous_only(registry):
    registry.upsert("a1", slug="mindmap")
    for sha in ("s1", "s2", "s3"):
        dropped = registry.add_release("a1", Release(sha=sha, deployed_at="2026-11-15T00:00:00Z", type="static"))
    record = registry.get("a1")
    assert dropped == ["s1"]
    assert (record.current_sha, record.previous_sha) == ("s3", "s2")
    assert sorted(record.releases) == ["s2", "s3"]

    registry.activate("a1", "s2")
    record = registry.get("a1")
    assert (record.current_sha, record.previous_sha) == ("s2", "s3")
    with pytest.raises(KeyError):
        registry.activate("a1", "gone")


def test_env_is_stored_beside_the_app_and_reports_changes(registry):
    registry.upsert("a1", slug="mindmap")
    assert registry.write_env("a1", {"EFP_APP_TOKEN": "t1"}) is True
    assert registry.write_env("a1", {"EFP_APP_TOKEN": "t1"}) is False
    assert registry.write_env("a1", {"EFP_APP_TOKEN": "t2"}) is True
    assert registry.read_env("a1") == {"EFP_APP_TOKEN": "t2"}


def test_lookups(registry):
    record, _ = registry.upsert("a1", slug="mindmap")
    assert registry.find_by_slug("mindmap") is record
    assert registry.find_by_uid(record.uid) is record
    assert registry.find_by_slug("nope") is None
    with pytest.raises(AppNotFound):
        registry.get("missing")
    assert registry.remove("a1") is record
    assert registry.all() == []


@pytest.mark.parametrize(
    "slug, ok",
    [("mindmap", True), ("a-b", True), ("ab", False), ("-ab", False), ("ab-", False), ("Mind", False), ("login", False), ("_efp", False), ("a" * 41, False), ("mindmap\n", False), ("\nmindmap", False)],
)
def test_slug_rules(slug, ok):
    assert is_valid_slug(slug) is ok


def test_settings_from_env():
    env = {
        "EFP_HOST_API_TOKEN": "secret",
        "EFP_PORTAL_INTERNAL_URL": "http://portal/",
        "EFP_PORTAL_PUBLIC_URL": "https://efp.example.com",
        "EFP_APPS_PUBLIC_URL": "https://app.example.com/",
        "EFP_HOST_ISOLATION_MODE": "hidden",
        "PIP_INDEX_URL": "https://pip.example.com/simple",
        "UNRELATED": "x",
    }
    settings = Settings.from_env(env)
    assert settings.is_hidden and not settings.is_sandbox
    assert settings.portal_internal_url == "http://portal"
    assert settings.apps_public_url == "https://app.example.com"
    assert dict(settings.passthrough_env) == {"PIP_INDEX_URL": "https://pip.example.com/simple"}

    with pytest.raises(SettingsError):
        Settings.from_env({})
    with pytest.raises(SettingsError):
        Settings.from_env({**env, "EFP_HOST_ISOLATION_MODE": "open"})
    with pytest.raises(SettingsError):
        Settings.from_env({**env, "EFP_APPS_PUBLIC_URL": "app.example.com"})
