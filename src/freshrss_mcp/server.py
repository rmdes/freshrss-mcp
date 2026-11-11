"""FreshRSS MCP Server implementation."""

import json
import logging
import os
from pathlib import Path
from typing import List, Optional, Dict, Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .client import FreshRSSClient, AuthenticationError

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("FreshRSS MCP Server")

DEFAULT_CONFIG = Path.home() / ".config" / "freshrss-mcp" / "instances.json"

# Instances added at runtime via freshrss_authenticate (name -> credentials)
runtime_instances: Dict[str, Dict[str, str]] = {}
# One authenticated client per instance name, created on first use
clients: Dict[str, FreshRSSClient] = {}
# Name of the instance all tools act on
active: Optional[str] = None


def load_instances() -> Dict[str, Dict[str, str]]:
    """Return configured instances: name -> {url, user, api_password}.

    Sources, later wins: legacy FRESHRSS_* env vars (as "default"),
    the JSON config file (FRESHRSS_CONFIG or ~/.config/freshrss-mcp/instances.json),
    then instances added at runtime. Re-read on every call so edits apply without restart.
    """
    instances: Dict[str, Dict[str, str]] = {}
    if os.getenv("FRESHRSS_URL"):
        instances["default"] = {
            "url": os.getenv("FRESHRSS_URL", ""),
            "user": os.getenv("FRESHRSS_EMAIL", ""),
            "api_password": os.getenv("FRESHRSS_API_PASSWORD", ""),
        }
    config = Path(os.getenv("FRESHRSS_CONFIG", DEFAULT_CONFIG)).expanduser()
    if config.is_file():
        instances.update(json.loads(config.read_text()))
    instances.update(runtime_instances)
    return instances


def active_name() -> Optional[str]:
    """Active instance: the switched-to one, else FRESHRSS_INSTANCE, else the first configured."""
    instances = load_instances()
    for name in (active, os.getenv("FRESHRSS_INSTANCE")):
        if name in instances:
            return name
    return next(iter(instances), None)


class AuthenticateParams(BaseModel):
    """Parameters for authentication."""
    name: Optional[str] = Field(None, description="Instance name to register these credentials under (defaults to the active instance)")
    base_url: Optional[str] = Field(None, description="FreshRSS instance URL (uses env if not provided)")
    email: Optional[str] = Field(None, description="User email (uses env if not provided)")
    api_password: Optional[str] = Field(None, description="API password (uses env if not provided)")


class GetArticlesParams(BaseModel):
    """Parameters for fetching articles."""
    folder: Optional[str] = Field(None, description="Folder/label name to filter by")
    feed_url: Optional[str] = Field(None, description="Feed to filter by: feed URL or feed_id")
    show_read: bool = Field(False, description="Include read articles")
    starred_only: bool = Field(False, description="Show only starred articles")
    count: int = Field(50, description="Number of articles to fetch (max ~1000)")
    order: str = Field("newest", description="Sort order: 'newest' or 'oldest'")
    continuation: Optional[str] = Field(None, description="Continuation token for pagination")


class MarkArticlesParams(BaseModel):
    """Parameters for marking articles."""
    article_ids: List[str] = Field(..., description="List of article IDs")


class AddLabelParams(BaseModel):
    """Parameters for adding labels."""
    article_ids: List[str] = Field(..., description="List of article IDs")
    label: str = Field(..., description="Label name to add")


class SubscribeParams(BaseModel):
    """Parameters for subscribing to a feed."""
    feed_url: str = Field(..., description="URL of the feed to subscribe to")
    title: Optional[str] = Field(None, description="Custom title for the feed")
    folder: Optional[str] = Field(None, description="Folder to add the feed to")


class UnsubscribeParams(BaseModel):
    """Parameters for unsubscribing from a feed."""
    feed_url: str = Field(..., description="Feed to unsubscribe from: feed URL or feed_id")


async def connect(name: str, cfg: Dict[str, str]) -> FreshRSSClient:
    """Authenticate against an instance and cache the client; state changes only on success."""
    client = FreshRSSClient(cfg["url"], cfg["user"], cfg["api_password"])
    try:
        await client.authenticate()
    except Exception:
        await client.close()
        raise
    old = clients.pop(name, None)
    if old:
        await old.close()
    clients[name] = client
    return client


async def ensure_authenticated(name: Optional[str] = None) -> FreshRSSClient:
    """Return an authenticated client for `name` (default: the active instance)."""
    name = name or active_name()
    if name is None:
        raise AuthenticationError(
            f"No FreshRSS instance configured. Create {DEFAULT_CONFIG}, "
            "set FRESHRSS_* environment variables, or call freshrss_authenticate."
        )
    if name in clients:
        return clients[name]
    return await connect(name, load_instances()[name])


@mcp.tool()
async def freshrss_list_instances() -> Dict[str, Any]:
    """List configured FreshRSS instances and which one is active."""
    current = active_name()
    return {
        "success": True,
        "active": current,
        "instances": [
            {
                "name": name,
                "url": cfg.get("url"),
                "user": cfg.get("user"),
                "active": name == current,
                "connected": name in clients,
            }
            for name, cfg in load_instances().items()
        ],
    }


class SwitchInstanceParams(BaseModel):
    """Parameters for switching instance."""
    name: str = Field(..., description="Instance name, as listed by freshrss_list_instances")


@mcp.tool()
async def freshrss_switch_instance(params: SwitchInstanceParams) -> Dict[str, Any]:
    """Switch the FreshRSS instance that all other tools act on."""
    global active
    instances = load_instances()
    if params.name not in instances:
        return {
            "success": False,
            "error": f"Unknown instance '{params.name}'. Known: {', '.join(instances) or 'none'}",
        }
    try:
        await ensure_authenticated(params.name)
    except Exception as e:
        logger.error(f"Failed to switch to {params.name}: {e}")
        return {"success": False, "error": str(e)}
    active = params.name
    return {
        "success": True,
        "active": params.name,
        "url": instances[params.name].get("url"),
    }


@mcp.tool()
async def freshrss_authenticate(params: AuthenticateParams) -> Dict[str, Any]:
    """Authenticate with a FreshRSS instance and make it active.

    Provided credentials are registered (for this session) under `name`,
    or the active instance. Without credentials, re-authenticates the
    configured instance `name` (or the active one).
    """
    global active
    name = params.name or active_name() or "default"
    cfg = dict(load_instances().get(name, {}))
    if params.base_url:
        cfg["url"] = params.base_url
    if params.email:
        cfg["user"] = params.email
    if params.api_password:
        cfg["api_password"] = params.api_password

    if not all(cfg.get(k) for k in ("url", "user", "api_password")):
        return {
            "success": False,
            "error": f"Missing credentials for instance '{name}'. Provide base_url, email, and api_password."
        }

    try:
        await connect(name, cfg)
        if params.base_url or params.email or params.api_password:
            runtime_instances[name] = cfg
        active = name

        return {
            "success": True,
            "message": "Successfully authenticated with FreshRSS",
            "name": name,
            "user": cfg["user"],
            "instance": cfg["url"]
        }
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_get_token() -> Dict[str, Any]:
    """Get edit token for write operations. Usually called automatically when needed."""
    try:
        client = await ensure_authenticated()
        token = await client.get_token()
        return {
            "success": True,
            "token": token,
            "message": "Token retrieved successfully"
        }
    except Exception as e:
        logger.error(f"Failed to get token: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_list_folders() -> Dict[str, Any]:
    """List all folders/categories/tags in FreshRSS."""
    try:
        client = await ensure_authenticated()
        tag_list = await client.get_tag_list()
        
        folders = []
        for tag in tag_list.folders:
            folders.append({
                "name": tag.label,
                "id": tag.id,
                "type": "folder"
            })
        
        return {
            "success": True,
            "folders": folders,
            "count": len(folders)
        }
    except Exception as e:
        logger.error(f"Failed to list folders: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_list_subscriptions() -> Dict[str, Any]:
    """List all subscribed feeds with their folders."""
    try:
        client = await ensure_authenticated()
        subscription_list = await client.get_subscription_list()
        
        subscriptions = []
        for sub in subscription_list.subscriptions:
            folders = [cat.label for cat in sub.categories if cat.is_label]
            subscriptions.append({
                "title": sub.title,
                "feed_id": sub.feed_id,
                "feed_url": sub.url,
                "id": sub.id,
                "folders": folders,
                "html_url": sub.htmlUrl,
                "icon_url": sub.iconUrl
            })
        
        return {
            "success": True,
            "subscriptions": subscriptions,
            "count": len(subscriptions)
        }
    except Exception as e:
        logger.error(f"Failed to list subscriptions: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_get_unread_count() -> Dict[str, Any]:
    """Get unread counts by feed and folder."""
    try:
        client = await ensure_authenticated()
        unread_counts = await client.get_unread_counts()
        
        # Organize counts by type
        feeds = []
        folders = []
        total_unread = 0
        
        for count in unread_counts:
            if count.id.startswith("feed/"):
                feeds.append({
                    "feed_id": count.id[5:],
                    "count": count.count
                })
            elif count.id.startswith("user/-/label/"):
                folders.append({
                    "folder": count.id.split("/")[-1],
                    "count": count.count
                })
            elif count.id == "user/-/state/com.google/reading-list":
                total_unread = count.count
        
        return {
            "success": True,
            "total_unread": total_unread,
            "feeds": feeds,
            "folders": folders
        }
    except Exception as e:
        logger.error(f"Failed to get unread counts: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_get_articles(params: GetArticlesParams) -> Dict[str, Any]:
    """Fetch articles with various filters."""
    try:
        client = await ensure_authenticated()
        
        # Determine stream ID
        if params.starred_only:
            stream_id = "user/-/state/com.google/starred"
        elif params.folder:
            stream_id = f"user/-/label/{params.folder}"
        elif params.feed_url:
            stream_id = f"feed/{params.feed_url}"
        else:
            stream_id = "user/-/state/com.google/reading-list"
        
        # Set exclude target for unread only
        exclude_target = None if params.show_read else "user/-/state/com.google/read"
        
        # Fetch articles
        stream = await client.get_stream_contents(
            stream_id=stream_id,
            count=params.count,
            order="d" if params.order == "newest" else "o",
            exclude_target=exclude_target,
            continuation=params.continuation
        )
        
        # Format articles
        articles = []
        for article in stream.items:
            articles.append({
                "id": article.id,
                "title": article.title,
                "url": article.url,
                "content": article.content,
                "summary": article.summary,
                "author": article.author,
                "published": article.published.isoformat() if article.published else None,
                "feed_title": article.feed_title,
                "feed_id": article.feed_id,
                "is_read": article.is_read,
                "is_starred": article.is_starred,
                "labels": [cat for cat in article.categories if cat.startswith("user/-/label/")]
            })
        
        return {
            "success": True,
            "articles": articles,
            "count": len(articles),
            "has_more": stream.has_more,
            "continuation": stream.continuation
        }
    except Exception as e:
        logger.error(f"Failed to get articles: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_mark_read(params: MarkArticlesParams) -> Dict[str, Any]:
    """Mark articles as read."""
    try:
        client = await ensure_authenticated()
        response = await client.mark_as_read(params.article_ids)
        
        return {
            "success": True,
            "message": f"Marked {len(params.article_ids)} article(s) as read",
            "status": response.status
        }
    except Exception as e:
        logger.error(f"Failed to mark articles as read: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_mark_unread(params: MarkArticlesParams) -> Dict[str, Any]:
    """Mark articles as unread."""
    try:
        client = await ensure_authenticated()
        response = await client.mark_as_unread(params.article_ids)
        
        return {
            "success": True,
            "message": f"Marked {len(params.article_ids)} article(s) as unread",
            "status": response.status
        }
    except Exception as e:
        logger.error(f"Failed to mark articles as unread: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_star_article(params: MarkArticlesParams) -> Dict[str, Any]:
    """Star articles."""
    try:
        client = await ensure_authenticated()
        response = await client.star_article(params.article_ids)
        
        return {
            "success": True,
            "message": f"Starred {len(params.article_ids)} article(s)",
            "status": response.status
        }
    except Exception as e:
        logger.error(f"Failed to star articles: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_unstar_article(params: MarkArticlesParams) -> Dict[str, Any]:
    """Unstar articles."""
    try:
        client = await ensure_authenticated()
        response = await client.unstar_article(params.article_ids)
        
        return {
            "success": True,
            "message": f"Unstarred {len(params.article_ids)} article(s)",
            "status": response.status
        }
    except Exception as e:
        logger.error(f"Failed to unstar articles: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_add_label(params: AddLabelParams) -> Dict[str, Any]:
    """Add label to articles."""
    try:
        client = await ensure_authenticated()
        response = await client.add_label(params.article_ids, params.label)
        
        return {
            "success": True,
            "message": f"Added label '{params.label}' to {len(params.article_ids)} article(s)",
            "status": response.status
        }
    except Exception as e:
        logger.error(f"Failed to add label: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_subscribe(params: SubscribeParams) -> Dict[str, Any]:
    """Subscribe to a new feed."""
    try:
        client = await ensure_authenticated()
        response = await client.subscribe(
            feed_url=params.feed_url,
            title=params.title,
            folder=params.folder
        )
        
        return {
            "success": True,
            "message": f"Successfully subscribed to {params.feed_url}",
            "status": response.status
        }
    except Exception as e:
        logger.error(f"Failed to subscribe: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def freshrss_unsubscribe(params: UnsubscribeParams) -> Dict[str, Any]:
    """Unsubscribe from a feed."""
    try:
        client = await ensure_authenticated()
        response = await client.unsubscribe(params.feed_url)
        
        return {
            "success": True,
            "message": f"Successfully unsubscribed from {params.feed_url}",
            "status": response.status
        }
    except Exception as e:
        logger.error(f"Failed to unsubscribe: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def main():
    """Run the MCP server."""
    import sys
    
    # Check for transport argument
    transport = "stdio"  # default
    port = 8000  # default port for HTTP
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--http":
            transport = "streamable-http"
            print(f"🚀 Starting FreshRSS MCP Server on http://localhost:{port}")
            print(f"📡 MCP endpoint (streamable HTTP): http://localhost:{port}/mcp")
        elif sys.argv[1] == "--sse":
            transport = "sse"
            print(f"🚀 Starting FreshRSS MCP Server with SSE on port {port}")
        elif sys.argv[1] == "--stdio":
            # No print here: stdout is the JSON-RPC channel in stdio mode.
            transport = "stdio"
        elif sys.argv[1] in ["-h", "--help"]:
            print("FreshRSS MCP Server")
            print("Usage:")
            print("  freshrss-mcp [--http|--sse|--stdio]")
            print("  freshrss-mcp --http    # HTTP transport on port 8000")
            print("  freshrss-mcp --sse     # Server-Sent Events transport")
            print("  freshrss-mcp --stdio   # Standard I/O transport (default)")
            return
    
    # Configure logging level
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))
    
    if transport == "streamable-http":
        # For HTTP transport, we need to set up the server differently
        import uvicorn
        
        # Create the HTTP app
        app = mcp.streamable_http_app()
        
        # Show startup logs
        logger.info(f"🚀 FreshRSS MCP Server starting on http://localhost:{port}")
        logger.info("📋 15 MCP tools loaded for FreshRSS management")
        
        # Use 0.0.0.0 when running in Docker to allow external connections
        host = "0.0.0.0" if os.getenv("DOCKER_CONTAINER", "").lower() == "true" else "localhost"
        
        uvicorn.run(
            app, 
            host=host, 
            port=port, 
            log_level=log_level.lower(),
            access_log=True
        )
    elif transport == "stdio":
        if len(sys.argv) > 1 and sys.argv[1] == "--stdio":
            logger.info("🚀 FreshRSS MCP Server starting with stdio transport")
        mcp.run(transport=transport)
    else:
        logger.info(f"🚀 FreshRSS MCP Server starting with {transport} transport")
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()