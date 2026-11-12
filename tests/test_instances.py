"""Multi-instance registry and switching."""

import json

import pytest

from freshrss_mcp import server
from freshrss_mcp.client import APIError, AuthenticationError, FreshRSSClient


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


async def test_queries_merge_into_env_instance(isolated, monkeypatch):
    monkeypatch.setenv("FRESHRSS_URL", "https://env.example")
    monkeypatch.setenv("FRESHRSS_EMAIL", "rick")
    monkeypatch.setenv("FRESHRSS_API_PASSWORD", "a")
    isolated.write_text(json.dumps({"default": {"queries": {"Libres": "https://env.example/api/query.php?t=X"}}}))
    assert server.load_instances()["default"]["url"] == "https://env.example"

    async def no_extension(self):
        raise APIError("HTTP 404: Not Found!")

    monkeypatch.setattr(FreshRSSClient, "list_user_queries", no_extension)
    listed = await server.freshrss_list_queries()
    assert listed["queries"] == [{"name": "Libres", "source": "share_url"}]
    assert listed["instance"] == "default" and "404" in listed["extension_error"]

    async def with_extension(self):
        return [{"name": "Libres", "get": "a"}, {"name": "Bots", "get": "c_3", "get_name": "News", "search": "x"}]

    monkeypatch.setattr(FreshRSSClient, "list_user_queries", with_extension)
    listed = await server.freshrss_list_queries()
    assert [(q["name"], q["source"]) for q in listed["queries"]] == [("Libres", "share_url"), ("Bots", "extension")]
    assert listed["queries"][1]["filter"] == "News" and "extension_error" not in listed

    # A server holding 5 articles that caps each response at 2 (like max_posts_per_rss).
    store = [{"id": f"tag:google.com,2005:reader/item/{n}", "title": str(n), "published": n} for n in range(5)]
    calls = []

    def page(count, offset):
        return {"title": "q", "items": store[offset:offset + min(count, 2)]}

    async def fake_share(self, url, count=50, offset=0, search=None, hours=None):
        calls.append(("share", url, count, offset, hours))
        return FreshRSSClient._parse_stream(page(count, offset), url)

    async def fake_ext(self, name, count=50, offset=0, search=None, hours=None):
        calls.append(("extension", name, count, offset, hours))
        return FreshRSSClient._parse_stream(page(count, offset), name)

    monkeypatch.setattr(FreshRSSClient, "get_query_contents", fake_share)
    monkeypatch.setattr(FreshRSSClient, "get_user_query_contents", fake_ext)
    res = await server.freshrss_get_query_articles(server.QueryArticlesParams(name="Libres", count=2))
    assert res["success"] and res["count"] == 2 and res["has_more"] and res["next_offset"] == 2
    assert calls == [("share", "https://env.example/api/query.php?t=X", 2, 0, None)]

    # Asked for 50, got 2 because of the cap: must still report more, via a 1-item probe.
    calls.clear()
    res = await server.freshrss_get_query_articles(server.QueryArticlesParams(name="Bots", hours=720))
    assert res["count"] == 2 and res["has_more"] and res["next_offset"] == 2
    assert calls == [("extension", "Bots", 50, 0, 720), ("extension", "Bots", 1, 2, 720)]

    # Last page: short and nothing after it.
    res = await server.freshrss_get_query_articles(server.QueryArticlesParams(name="Bots", offset=4))
    assert res["count"] == 1 and not res["has_more"] and res["next_offset"] == 5
