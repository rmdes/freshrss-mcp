"""FreshRSS API client implementation."""

import logging
from typing import Dict, List, Optional, Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from datetime import datetime

import httpx
from httpx import Response

from .models import (
    Article,
    AuthResponse,
    Category,
    EditResponse,
    StreamContents,
    Subscription,
    SubscriptionList,
    TagList,
    UnreadCount,
)

logger = logging.getLogger(__name__)


class FreshRSSError(Exception):
    """Base exception for FreshRSS API errors."""
    pass


class AuthenticationError(FreshRSSError):
    """Authentication failed."""
    pass


class APIError(FreshRSSError):
    """API request failed."""
    pass


class FreshRSSClient:
    """Client for interacting with FreshRSS Google Reader API."""
    
    def __init__(
        self,
        base_url: str,
        email: str,
        api_password: str,
        timeout: float = 30.0,
    ):
        """Initialize FreshRSS client.
        
        Args:
            base_url: FreshRSS instance URL (e.g., https://freshrss.example.com)
            email: User email
            api_password: API password (not the main password)
            timeout: Request timeout in seconds
        """
        # Accept both the instance root and the full greader endpoint URL.
        self.base_url = base_url.rstrip("/").removesuffix("/api/greader.php")
        self.api_url = f"{self.base_url}/api/greader.php"
        self.email = email
        self.api_password = api_password
        self.timeout = timeout
        self.auth_token: Optional[str] = None
        self.edit_token: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=timeout)
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
    
    def _build_url(self, endpoint: str) -> str:
        """Build full API URL."""
        if endpoint.startswith("/"):
            endpoint = endpoint[1:]
        return f"{self.api_url}/{endpoint}"
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data=None,
        auth_required: bool = True,
    ) -> Response:
        """Make an API request.

        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            data: Form data for POST requests (dict or list of tuples)
            auth_required: Whether authentication is required

        Returns:
            HTTP response

        Raises:
            AuthenticationError: If authentication is required but not available
            APIError: If the request fails
        """
        headers = {}
        if auth_required:
            if not self.auth_token:
                raise AuthenticationError("Not authenticated. Call authenticate() first.")
            headers["Authorization"] = f"GoogleLogin auth={self.auth_token}"

        url = endpoint if endpoint.startswith(("http://", "https://")) else self._build_url(endpoint)

        # httpx 0.28 bug: passing a list of tuples as `data` to AsyncClient
        # raises "Attempted to send a sync request with an AsyncClient instance".
        # Workaround: manually urlencode and send as raw content.
        kwargs: Dict[str, Any] = {}
        if isinstance(data, list):
            kwargs["content"] = urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            kwargs["data"] = data

        try:
            response = await self._client.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
            raise APIError(f"HTTP {e.response.status_code}: {e.response.text}") from e
        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise APIError(f"Request failed: {e}") from e
    
    async def authenticate(self) -> AuthResponse:
        """Authenticate with FreshRSS.
        
        Returns:
            Authentication response with tokens
            
        Raises:
            AuthenticationError: If authentication fails
        """
        try:
            response = await self._request(
                method="POST",
                endpoint="accounts/ClientLogin",
                data={
                    "Email": self.email,
                    "Passwd": self.api_password,
                },
                auth_required=False,
            )
            
            # Parse response (format: key=value per line)
            lines = response.text.strip().split("\n")
            auth_data = {}
            for line in lines:
                if "=" in line:
                    key, value = line.split("=", 1)
                    auth_data[key] = value
            
            if "Auth" not in auth_data:
                raise AuthenticationError("No auth token in response")
            
            auth_response = AuthResponse(
                SID=auth_data.get("SID", ""),
                Auth=auth_data["Auth"],
            )
            
            self.auth_token = auth_response.token
            logger.info("Successfully authenticated with FreshRSS")
            return auth_response
            
        except APIError as e:
            raise AuthenticationError(f"Authentication failed: {e}") from e
    
    async def get_token(self) -> str:
        """Get edit token for write operations.
        
        Returns:
            Edit token
            
        Raises:
            APIError: If token retrieval fails
        """
        response = await self._request("GET", "reader/api/0/token")
        self.edit_token = response.text.strip()
        return self.edit_token
    
    async def get_subscription_list(self) -> SubscriptionList:
        """Get list of subscribed feeds.
        
        Returns:
            List of subscriptions
        """
        response = await self._request("GET", "reader/api/0/subscription/list", params={"output": "json"})
        data = response.json()
        return SubscriptionList(subscriptions=[
            Subscription(**sub) for sub in data.get("subscriptions", [])
        ])
    
    async def get_tag_list(self) -> TagList:
        """Get list of tags/labels/folders.
        
        Returns:
            List of tags
        """
        response = await self._request("GET", "reader/api/0/tag/list", params={"output": "json"})
        data = response.json()
        tags = []
        for tag in data.get("tags", []):
            tags.append(Category(
                id=tag["id"],
                label=tag.get("sortid", tag["id"].split("/")[-1]),
                type="tag" if "label" in tag["id"] else "state"
            ))
        return TagList(tags=tags)
    
    async def get_unread_counts(self) -> List[UnreadCount]:
        """Get unread counts for all feeds and categories.
        
        Returns:
            List of unread counts
        """
        response = await self._request("GET", "reader/api/0/unread-count", params={"output": "json"})
        data = response.json()
        return [
            UnreadCount(**item) for item in data.get("unreadcounts", [])
        ]
    
    async def get_stream_contents(
        self,
        stream_id: str = "user/-/state/com.google/reading-list",
        count: int = 50,
        order: str = "d",  # d=descending (newest first), o=ascending
        start_time: Optional[int] = None,
        continuation: Optional[str] = None,
        exclude_target: Optional[str] = None,
        include_target: Optional[str] = None,
    ) -> StreamContents:
        """Get articles from a stream.
        
        Args:
            stream_id: Stream to fetch (reading-list, starred, or user/-/label/FolderName)
            count: Number of items to fetch (max ~1000)
            order: Sort order (d=descending, o=ascending)
            start_time: Unix timestamp to start from
            continuation: Continuation token for pagination
            exclude_target: Exclude articles with this state (e.g., user/-/state/com.google/read)
            include_target: Include only articles with this state
            
        Returns:
            Stream contents with articles
        """
        params = {
            "output": "json",
            "n": count,
            "r": order,
        }
        
        if start_time:
            params["ot"] = start_time
        if continuation:
            params["c"] = continuation
        if exclude_target:
            params["xt"] = exclude_target
        if include_target:
            params["it"] = include_target
        
        # URL encode the stream ID and append to endpoint. For feed/<url>, FreshRSS
        # matches REQUEST_URI against "/stream/contents/feed/<urlencoded>", so the
        # slash after "feed" must stay literal.
        if stream_id.startswith("feed/"):
            encoded_stream = "feed/" + quote(stream_id[5:], safe="")
        else:
            encoded_stream = quote(stream_id, safe="")
        endpoint = f"reader/api/0/stream/contents/{encoded_stream}"
        
        response = await self._request("GET", endpoint, params=params)
        return self._parse_stream(response.json(), stream_id)

    async def get_query_contents(
        self,
        query_url: str,
        count: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
    ) -> StreamContents:
        """Get articles from a shared user query (p/api/query.php).

        FreshRSS only exposes user queries that are shared; the share URL carries
        its own token, so no API auth is needed. Any share URL works (f=rss, f=greader...):
        it is rewritten to the greader JSON format, which needs "share as OPML" enabled.

        Args:
            query_url: Share URL of the user query
            count: Number of items to fetch
            offset: Number of items to skip (the endpoint has no continuation token)
            search: Extra FreshRSS search, ANDed with the query's own filter
        """
        parts = urlsplit(query_url)
        params: Dict[str, Any] = dict(parse_qsl(parts.query))
        params.update({"f": "greader", "nb": count})
        if offset:
            params["offset"] = offset
        if search:
            params["search"] = search
        url = urlunsplit(parts._replace(query=""))
        response = await self._request("GET", url, params=params, auth_required=False)
        return self._parse_stream(response.json(), url)

    async def list_user_queries(self) -> List[Dict[str, Any]]:
        """List all saved user queries via the UserQueries extension.

        Requires https://github.com/rmdes/freshrss-user-queries installed and enabled on the instance.
        """
        response = await self._request(
            "GET", f"{self.base_url}/api/misc.php", params={"ext": "UserQueries"}
        )
        return response.json().get("queries", [])

    async def get_user_query_contents(
        self,
        name: str,
        count: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
    ) -> StreamContents:
        """Run a saved user query by name via the UserQueries extension (shared or not)."""
        params: Dict[str, Any] = {"ext": "UserQueries", "name": name, "nb": count}
        if offset:
            params["offset"] = offset
        if search:
            params["search"] = search
        response = await self._request("GET", f"{self.base_url}/api/misc.php", params=params)
        return self._parse_stream(response.json(), name)

    @staticmethod
    def _parse_stream(data: Dict[str, Any], stream_id: str) -> StreamContents:
        """Parse a Google Reader stream JSON payload into StreamContents."""
        articles = []
        for item in data.get("items", []):
            # Convert timestamps
            published = None
            if "published" in item:
                published = datetime.fromtimestamp(item["published"])
            elif "crawlTimeMsec" in item:
                published = datetime.fromtimestamp(int(item["crawlTimeMsec"]) / 1000)
            
            article = Article(
                id=item["id"],
                title=item.get("title", ""),
                published=published or datetime.now(),
                updated=datetime.fromtimestamp(item["updated"]) if "updated" in item else None,
                author=item.get("author"),
                content=item.get("content", {}).get("content") if "content" in item else None,
                summary=item.get("summary", {}).get("content") if "summary" in item else None,
                categories=item.get("categories", []),
                origin=item.get("origin"),
                alternate=item.get("alternate", []),
                crawlTimeMsec=item.get("crawlTimeMsec"),
                timestampUsec=item.get("timestampUsec"),
            )
            articles.append(article)
        
        return StreamContents(
            id=data.get("id", stream_id),
            title=data.get("title"),
            items=articles,
            continuation=data.get("continuation"),
            updated=data.get("updated"),
        )
    
    async def mark_as_read(self, item_ids: List[str]) -> EditResponse:
        """Mark articles as read.
        
        Args:
            item_ids: List of article IDs to mark as read
            
        Returns:
            Edit response
        """
        if not self.edit_token:
            await self.get_token()
        
        # Use list of tuples to allow multiple "i" keys for batch operations.
        # A dict would overwrite duplicate keys, sending only the last item_id.
        data = [
            ("T", self.edit_token),
            ("a", "user/-/state/com.google/read"),
        ]
        for item_id in item_ids:
            data.append(("i", item_id))

        response = await self._request("POST", "reader/api/0/edit-tag", data=data)
        return EditResponse(status=response.text.strip())

    async def mark_as_unread(self, item_ids: List[str]) -> EditResponse:
        """Mark articles as unread.
        
        Args:
            item_ids: List of article IDs to mark as unread
            
        Returns:
            Edit response
        """
        if not self.edit_token:
            await self.get_token()
        
        data = [
            ("T", self.edit_token),
            ("a", "user/-/state/com.google/kept-unread"),
            ("r", "user/-/state/com.google/read"),
        ]
        for item_id in item_ids:
            data.append(("i", item_id))

        response = await self._request("POST", "reader/api/0/edit-tag", data=data)
        return EditResponse(status=response.text.strip())

    async def star_article(self, item_ids: List[str]) -> EditResponse:
        """Star articles.
        
        Args:
            item_ids: List of article IDs to star
            
        Returns:
            Edit response
        """
        if not self.edit_token:
            await self.get_token()
        
        data = [
            ("T", self.edit_token),
            ("a", "user/-/state/com.google/starred"),
        ]
        for item_id in item_ids:
            data.append(("i", item_id))

        response = await self._request("POST", "reader/api/0/edit-tag", data=data)
        return EditResponse(status=response.text.strip())

    async def unstar_article(self, item_ids: List[str]) -> EditResponse:
        """Unstar articles.
        
        Args:
            item_ids: List of article IDs to unstar
            
        Returns:
            Edit response
        """
        if not self.edit_token:
            await self.get_token()
        
        data = [
            ("T", self.edit_token),
            ("r", "user/-/state/com.google/starred"),
        ]
        for item_id in item_ids:
            data.append(("i", item_id))

        response = await self._request("POST", "reader/api/0/edit-tag", data=data)
        return EditResponse(status=response.text.strip())

    async def add_label(self, item_ids: List[str], label: str) -> EditResponse:
        """Add label to articles.
        
        Args:
            item_ids: List of article IDs
            label: Label name (without user/-/label/ prefix)
            
        Returns:
            Edit response
        """
        if not self.edit_token:
            await self.get_token()
        
        data = [
            ("T", self.edit_token),
            ("a", f"user/-/label/{label}"),
        ]
        for item_id in item_ids:
            data.append(("i", item_id))

        response = await self._request("POST", "reader/api/0/edit-tag", data=data)
        return EditResponse(status=response.text.strip())
    
    async def subscribe(self, feed_url: str, title: Optional[str] = None, folder: Optional[str] = None) -> EditResponse:
        """Subscribe to a new feed.
        
        Args:
            feed_url: URL of the feed
            title: Optional custom title
            folder: Optional folder name
            
        Returns:
            Edit response
        """
        if not self.edit_token:
            await self.get_token()
        
        data = {
            "T": self.edit_token,
            "ac": "subscribe",
            "s": f"feed/{feed_url}",
        }
        
        if title:
            data["t"] = title
        if folder:
            data["a"] = f"user/-/label/{folder}"
        
        response = await self._request("POST", "reader/api/0/subscription/edit", data=data)
        return EditResponse(status=response.text.strip())
    
    async def unsubscribe(self, feed_url: str) -> EditResponse:
        """Unsubscribe from a feed.
        
        Args:
            feed_url: URL of the feed
            
        Returns:
            Edit response
        """
        if not self.edit_token:
            await self.get_token()
        
        data = {
            "T": self.edit_token,
            "ac": "unsubscribe",
            "s": f"feed/{feed_url}",
        }
        
        response = await self._request("POST", "reader/api/0/subscription/edit", data=data)
        return EditResponse(status=response.text.strip())
