"""Multi-instance registry and switching."""

import json

import pytest

from freshrss_mcp import server
from freshrss_mcp.client import AuthenticationError, FreshRSSClient


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for var in ("FRESHRSS_URL", "FRESHRSS_EMAIL", "FRESHRSS_API_PASSWORD", "FRESHRSS_INSTANCE"):
        monkeypatch.delenv(var, raising=False)
    config = tmp_path / "instances.json"
    config.write_text(json.dumps({
        "skyfleet": {"url": "https://rss.skyfleet.blue", "user": "rick", "api_password": "a"},
        "other": {"url": "https://other.example/api/greader.php", "user": "bob", "api_password": "b"},
    }))
    monkeypatch.setenv("FRESHRSS_CONFIG", str(config))
    monkeypatch.setattr(server, "active", None)
    monkeypatch.setattr(server, "clients", {})
    monkeypatch.setattr(server, "runtime_instances", {})

    async def fake_auth(self):
        if self.api_password == "bad":
            raise AuthenticationError("nope")
        self.auth_token = "tok"

    monkeypatch.setattr(FreshRSSClient, "authenticate", fake_auth)
    return config


def test_env_instance_merges_and_first_is_default(monkeypatch):
    monkeypatch.setenv("FRESHRSS_URL", "https://env.example")
    assert list(server.load_instances()) == ["default", "skyfleet", "other"]
    assert server.active_name() == "default"
    monkeypatch.setenv("FRESHRSS_INSTANCE", "other")
    assert server.active_name() == "other"


def test_greader_suffix_is_normalized():
    client = FreshRSSClient("https://other.example/api/greader.php/", "u", "p")
    assert client.api_url == "https://other.example/api/greader.php"


async def test_switch_routes_clients_per_instance():
    first = await server.ensure_authenticated()
    assert first.base_url == "https://rss.skyfleet.blue"

    res = await server.freshrss_switch_instance(server.SwitchInstanceParams(name="other"))
    assert res["success"] and server.active_name() == "other"
    assert (await server.ensure_authenticated()).base_url == "https://other.example"
    assert set(server.clients) == {"skyfleet", "other"}

    listed = await server.freshrss_list_instances()
    assert [i["name"] for i in listed["instances"] if i["active"]] == ["other"]
    assert all("api_password" not in i for i in listed["instances"])


async def test_switch_unknown_or_failing_keeps_active(isolated):
    res = await server.freshrss_switch_instance(server.SwitchInstanceParams(name="nope"))
    assert not res["success"] and server.active_name() == "skyfleet"

    data = json.loads(isolated.read_text())
    data["broken"] = {"url": "https://x", "user": "x", "api_password": "bad"}
    isolated.write_text(json.dumps(data))
    res = await server.freshrss_switch_instance(server.SwitchInstanceParams(name="broken"))
    assert not res["success"] and server.active_name() == "skyfleet"
    assert "broken" not in server.clients


async def test_authenticate_with_bad_creds_changes_nothing():
    res = await server.freshrss_authenticate(server.AuthenticateParams(
        name="adhoc", base_url="https://y", email="y", api_password="bad"))
    assert not res["success"]
    assert "adhoc" not in server.load_instances() and server.active_name() == "skyfleet"

    res = await server.freshrss_authenticate(server.AuthenticateParams(
        name="adhoc", base_url="https://y", email="y", api_password="ok"))
    assert res["success"] and server.active_name() == "adhoc"
