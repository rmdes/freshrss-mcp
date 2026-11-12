"""Stream ID encoding sent to the greader API."""

from freshrss_mcp.client import FreshRSSClient


class FakeResponse:
    def json(self):
        return {}


async def test_stream_id_encoding(monkeypatch):
    client = FreshRSSClient("https://rss.example", "u", "p")
    seen = []

    async def fake_request(self, method, endpoint, params=None, **kw):
        seen.append(endpoint)
        return FakeResponse()

    monkeypatch.setattr(FreshRSSClient, "_request", fake_request)
    await client.get_stream_contents("feed/https://x.example/rss?q=a b&hl=en")
    await client.get_stream_contents("user/-/label/Tech News")

    # FreshRSS regex needs a literal "/stream/contents/feed/" before the encoded URL.
    assert seen[0] == "reader/api/0/stream/contents/feed/https%3A%2F%2Fx.example%2Frss%3Fq%3Da%20b%26hl%3Den"
    assert seen[1] == "reader/api/0/stream/contents/user%2F-%2Flabel%2FTech%20News"


async def test_query_url_is_rewritten_to_greader(monkeypatch):
    client = FreshRSSClient("https://rss.example", "u", "p")
    seen = []

    async def fake_request(self, method, endpoint, params=None, auth_required=True, **kw):
        seen.append((endpoint, params, auth_required))
        return FakeResponse()

    monkeypatch.setattr(FreshRSSClient, "_request", fake_request)
    await client.get_query_contents(
        "https://rss.example/api/query.php?user=rick&t=TOK&f=rss", count=5, offset=10, search="intitle:x"
    )
    assert seen == [(
        "https://rss.example/api/query.php",
        {"user": "rick", "t": "TOK", "f": "greader", "nb": 5, "offset": 10, "search": "intitle:x"},
        False,
    )]


async def test_user_query_action_form_encoding(monkeypatch):
    client = FreshRSSClient("https://rss.example", "u", "p")
    seen = []

    class JsonResponse:
        def json(self):
            return {"query": {"id": 3}}

    async def fake_request(self, method, endpoint, params=None, data=None, **kw):
        seen.append((method, endpoint, params, data))
        return JsonResponse()

    monkeypatch.setattr(FreshRSSClient, "_request", fake_request)
    out = await client.user_query_action(
        "create", name="Libres", category="MediasLibres", state=["read", "unread"],
        share_rss=True, share_opml=False, search=None, id=0,
    )
    assert out == {"query": {"id": 3}}
    assert seen == [("POST", "https://rss.example/api/misc.php", {"ext": "UserQueries"}, {
        "action": "create", "name": "Libres", "category": "MediasLibres", "state": "read,unread",
        "share_rss": "1", "share_opml": "0", "id": "0",
    })]
