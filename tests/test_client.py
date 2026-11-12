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


def test_future_published_dates_are_clamped_to_crawl_time():
    crawl = 1_800_000_000  # seconds
    stream = FreshRSSClient._parse_stream({"items": [
        {"id": "future", "title": "f", "published": crawl + 3 * 86400, "crawlTimeMsec": str(crawl * 1000)},
        {"id": "normal", "title": "n", "published": crawl - 3600, "crawlTimeMsec": str(crawl * 1000)},
        {"id": "no-crawl", "title": "x", "published": crawl + 86400},
    ]}, "s")
    published = {a.id: a.published.timestamp() for a in stream.items}
    assert published == {"future": crawl, "normal": crawl - 3600, "no-crawl": crawl + 86400}


async def test_hours_window_reaches_both_query_endpoints(monkeypatch):
    client = FreshRSSClient("https://rss.example", "u", "p")
    seen = []

    async def fake_request(self, method, endpoint, params=None, **kw):
        seen.append(params)
        return FakeResponse()

    monkeypatch.setattr(FreshRSSClient, "_request", fake_request)
    await client.get_query_contents("https://rss.example/api/query.php?user=u&t=T", count=400, hours=720)
    await client.get_user_query_contents("Bots", count=400, offset=400, hours=720)
    assert seen[0] == {"user": "u", "t": "T", "f": "greader", "nb": 400, "hours": 720}
    assert seen[1] == {"ext": "UserQueries", "name": "Bots", "nb": 400, "offset": 400, "hours": 720}
