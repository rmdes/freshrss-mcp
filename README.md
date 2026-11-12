# FreshRSS MCP Server

A Model Context Protocol (MCP) server for [FreshRSS](https://www.freshrss.org/), the self-hosted RSS feed aggregator. This server allows LLMs and other MCP clients to interact with your FreshRSS instance to manage feeds, read articles, and organize your RSS content.

## Features

- 🔐 **Authentication**: Secure connection to your FreshRSS instance
- 📁 **Folder Management**: List and organize feeds in folders
- 📰 **Article Reading**: Fetch articles with advanced filtering options
- ✅ **Article Management**: Mark articles as read/unread, star/unstar
- 🏷️ **Label System**: Add labels to articles for organization
- 📡 **Feed Management**: Subscribe/unsubscribe from RSS feeds
- 📊 **Unread Counts**: Get unread statistics by feed and folder

## Installation

### Using pip

```bash
pip install freshrss-mcp
```

### From source

```bash
git clone https://github.com/yourusername/freshrss-mcp.git
cd freshrss-mcp
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e .
```

## Configuration

### Environment Variables

Create a `.env` file or set these environment variables:

```bash
FRESHRSS_URL=https://your-freshrss-instance.com
FRESHRSS_EMAIL=your-email@example.com
FRESHRSS_API_PASSWORD=your-api-password
```

**Important**: The `FRESHRSS_API_PASSWORD` is NOT your regular FreshRSS password. You need to:
1. Enable API access in FreshRSS Settings → Authentication
2. Set an API password in your Profile settings

### Multiple Instances

List instances in `~/.config/freshrss-mcp/instances.json` (or the path in `FRESHRSS_CONFIG`).
The file is re-read on every call, so new instances show up without a restart:

```json
{
  "skyfleet": {"url": "https://rss.example.com", "user": "rick", "api_password": "..."},
  "work":     {"url": "https://rss.work.example", "user": "rick", "api_password": "..."}
}
```

Env vars above, if set, become an extra instance named `default`. The active instance is,
in order: the last one switched to, `FRESHRSS_INSTANCE`, or the first one listed.
Use `freshrss_list_instances` and `freshrss_switch_instance`; every other tool acts on the active one.

Entries merge per key, so to add something to the env-based instance only, write e.g.
`{"default": {"queries": {...}}}` without repeating credentials.

### Saved User Queries

FreshRSS's APIs cannot list or run user queries. Two ways to reach them:

**1. The UserQueries extension (recommended: all queries, automatically).**
[freshrss-user-queries](https://github.com/rmdes/freshrss-user-queries) (v0.1.0+; v0.2.0+ to create/edit/share) adds
`/api/misc.php?ext=UserQueries`, authenticated with the same Google Reader API login the MCP
already uses. It lists every saved query (shared or not) and runs any of them by name.
Install it on each FreshRSS instance per its README (clone into `extensions/xExtension-UserQueries`,
then enable **UserQueries** in Settings → Extensions as admin).

**2. Share URLs (no server changes, one query at a time).** Shared queries are also served by
`/api/query.php`. Enable **share as OPML** on the query (required for the JSON format the MCP
uses), then add its share URL under the instance; configured names take precedence:

```json
{"default": {"queries": {"MediasLibres": "https://rss.example.com/api/query.php?user=rick&t=TOKEN&f=rss"}}}
```

Any share URL works (`f=rss`, `f=greader`, ...); it is rewritten to `f=greader`. The token grants
read access to that query without login, so keep the file private (`chmod 600`).

## Running the Server

### Transport Modes

The FreshRSS MCP server supports multiple transport protocols:

#### 1. Stdio Mode (Default - for Claude Desktop)
```bash
# Activate virtual environment
source venv/bin/activate

# Run with stdio transport (silent, for MCP clients)
freshrss-mcp

# Or explicitly
freshrss-mcp --stdio
```

#### 2. HTTP Mode (for web integration)
```bash
# Activate virtual environment
source venv/bin/activate

# Run streamable HTTP server on port 8000
freshrss-mcp --http
```

**Output:**
```
INFO:freshrss_mcp.server:🚀 FreshRSS MCP Server starting on http://localhost:8000
INFO:freshrss_mcp.server:📋 13 MCP tools loaded for FreshRSS management
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
```

**Available endpoints:**
- 🌐 **HTTP**: `http://localhost:8000`
- 🔌 **WebSocket**: `ws://localhost:8000/ws`
- ❤️ **Health Check**: `http://localhost:8000/health`

#### 3. Server-Sent Events Mode
```bash
freshrss-mcp --sse
```

#### 4. Help
```bash
freshrss-mcp --help
```

### Claude Desktop Configuration

#### For Stdio Mode (Recommended)
Add the FreshRSS MCP server to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "freshrss": {
      "command": "freshrss-mcp",
      "env": {
        "FRESHRSS_URL": "https://your-freshrss-instance.com",
        "FRESHRSS_EMAIL": "your-email@example.com",
        "FRESHRSS_API_PASSWORD": "your-api-password"
      }
    }
  }
}
```

#### For HTTP Mode
```json
{
  "mcpServers": {
    "freshrss": {
      "command": "freshrss-mcp",
      "args": ["--http"],
      "env": {
        "FRESHRSS_URL": "https://your-freshrss-instance.com",
        "FRESHRSS_EMAIL": "your-email@example.com",
        "FRESHRSS_API_PASSWORD": "your-api-password"
      }
    }
  }
}
```

## Available Tools

### Authentication

#### `freshrss_authenticate`
Authenticate with your FreshRSS instance. Can use environment variables or explicit parameters.

```python
# Using environment variables
await freshrss_authenticate()

# Using explicit parameters
await freshrss_authenticate({
    "base_url": "https://freshrss.example.com",
    "email": "user@example.com",
    "api_password": "your-api-password"
})
```

#### `freshrss_list_instances`
List configured instances (name, url, user, active, connected). Passwords are never returned.

#### `freshrss_switch_instance`
Switch the instance all other tools act on. Authenticates first; on failure the active instance is unchanged.
```python
freshrss_switch_instance({"name": "work"})
```

### User Queries

#### `freshrss_list_queries`
List the active instance's user queries: all of them from the UserQueries extension, plus any
configured share URLs. If the extension is missing, `extension_error` says why.

#### `freshrss_get_query_articles`
Fetch a query's articles. Pages by `offset` (the endpoint has no continuation token); `search`
narrows the query with FreshRSS search syntax, ANDed with the query's own filter; `hours` keeps only
articles received in the last N hours. FreshRSS returns at most `max_posts_per_rss` items per call
(400 by default) whatever `count` is: keep calling with `next_offset` while `has_more` is true.
Published dates later than the moment FreshRSS fetched the article are clamped to that fetch time.
```python
freshrss_get_query_articles({"name": "MediasLibres", "count": 20, "offset": 20, "search": "intitle:police"})
freshrss_get_query_articles({"name": "MediasLibres", "count": 400, "hours": 720})  # last 30 days, page 1
```

#### `freshrss_create_query` / `freshrss_update_query` / `freshrss_delete_query`
Manage saved queries (needs the UserQueries extension v0.2.0+). Create one on a folder, toggle
sharing, and get the public links back (`html`/`rss` with `share_rss`, `greader`/`opml` with
`share_opml`). Update and delete take the query's `id` or a unique `name`; update only changes the
fields given, and keeps the share token so links survive turning sharing off and on.
```python
freshrss_create_query({"name": "MediasLibres", "folder": "MediasLibres", "share_rss": True, "share_opml": True})
freshrss_update_query({"name": "MediasLibres", "share_opml": False})
freshrss_delete_query({"id": 12})
```

### Folder Management

#### `freshrss_list_folders`
List all folders/categories in your FreshRSS instance.

```python
result = await freshrss_list_folders()
# Returns: {"folders": [{"name": "Tech", "id": "user/-/label/Tech", "type": "folder"}], "count": 1}
```

#### `freshrss_list_subscriptions`
List all subscribed feeds with their folder assignments.

```python
result = await freshrss_list_subscriptions()
# Returns detailed subscription information including folders
```

### Article Reading

#### `freshrss_get_articles`
Fetch articles with various filtering options.

```python
# Get unread articles from all feeds
await freshrss_get_articles({"show_read": false, "count": 50})

# Get articles from specific folder
await freshrss_get_articles({"folder": "Tech", "count": 20})

# Get starred articles
await freshrss_get_articles({"starred_only": true})

# Get articles from specific feed
await freshrss_get_articles({"feed_url": "https://example.com/feed.xml"})

# Pagination
await freshrss_get_articles({"count": 50, "continuation": "continuation_token"})
```

#### `freshrss_get_unread_count`
Get unread article counts organized by feed and folder.

```python
result = await freshrss_get_unread_count()
# Returns total unread count plus breakdowns by feed and folder
```

### Article Management

#### `freshrss_mark_read`
Mark one or more articles as read.

```python
await freshrss_mark_read({
    "article_ids": ["tag:google.com,2005:reader/item/..."]
})
```

#### `freshrss_mark_unread`
Mark one or more articles as unread.

```python
await freshrss_mark_unread({
    "article_ids": ["tag:google.com,2005:reader/item/..."]
})
```

#### `freshrss_star_article`
Star one or more articles.

```python
await freshrss_star_article({
    "article_ids": ["tag:google.com,2005:reader/item/..."]
})
```

#### `freshrss_unstar_article`
Unstar one or more articles.

```python
await freshrss_unstar_article({
    "article_ids": ["tag:google.com,2005:reader/item/..."]
})
```

#### `freshrss_add_label`
Add a label to one or more articles.

```python
await freshrss_add_label({
    "article_ids": ["tag:google.com,2005:reader/item/..."],
    "label": "Important"
})
```

### Feed Management

#### `freshrss_subscribe`
Subscribe to a new RSS feed.

```python
# Basic subscription
await freshrss_subscribe({
    "feed_url": "https://example.com/feed.xml"
})

# With custom title and folder
await freshrss_subscribe({
    "feed_url": "https://example.com/feed.xml",
    "title": "Example Blog",
    "folder": "Tech"
})
```

#### `freshrss_unsubscribe`
Unsubscribe from a feed.

```python
await freshrss_unsubscribe({
    "feed_url": "https://example.com/feed.xml"
})
```

## Example Usage

Here's a complete example of using the FreshRSS MCP server:

```python
# 1. Authenticate
await freshrss_authenticate()

# 2. List folders
folders = await freshrss_list_folders()
print(f"You have {folders['count']} folders")

# 3. Get unread counts
counts = await freshrss_get_unread_count()
print(f"Total unread: {counts['total_unread']}")

# 4. Fetch unread articles from Tech folder
articles = await freshrss_get_articles({
    "folder": "Tech",
    "show_read": false,
    "count": 10
})

# 5. Mark first article as read
if articles['articles']:
    await freshrss_mark_read({
        "article_ids": [articles['articles'][0]['id']]
    })

# 6. Star an interesting article
await freshrss_star_article({
    "article_ids": [articles['articles'][1]['id']]
})
```

## Development

### Quick Start in Virtual Environment

```bash
# Clone and setup
git clone https://github.com/yourusername/freshrss-mcp.git
cd freshrss-mcp

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env with your FreshRSS credentials

# Test the installation
python test_direct.py

# Run HTTP server
freshrss-mcp --http
```

### Running Tests

```bash
pytest tests/
```

### Code Style

This project uses Black for code formatting and Ruff for linting:

```bash
black src/
ruff check src/
```

### Development Commands

```bash
# Test all transport modes
freshrss-mcp --help
freshrss-mcp --stdio    # For MCP clients
freshrss-mcp --http     # HTTP server on port 8000
freshrss-mcp --sse      # Server-Sent Events

# Test API client directly
python test_direct.py

# Install development dependencies
pip install -e ".[dev]"
```

## API Implementation

This MCP server implements the [Google Reader API](https://freshrss.github.io/FreshRSS/en/developers/06_GoogleReader_API.html) as supported by FreshRSS. The implementation includes:

- Authentication via ClientLogin
- Stream contents for article fetching
- Edit tag operations for marking read/starred
- Subscription management
- Tag/folder listing

## Troubleshooting

### Authentication Issues

1. **"No auth token in response"**: Make sure you're using the API password, not your regular password
2. **HTTP 404 errors**: Check that your FreshRSS URL is correct and includes the protocol (https://)
3. **API not enabled**: Ensure API access is enabled in FreshRSS Settings → Authentication

### Performance Tips

- Use pagination with `continuation` tokens for large article lists
- Filter by folder or feed to reduce response size
- Set appropriate `count` values (max ~1000 per request)

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request

## License

MIT License - see LICENSE file for details

## Acknowledgments

- [FreshRSS](https://www.freshrss.org/) for the excellent RSS reader
- [Model Context Protocol](https://modelcontextprotocol.io/) for the MCP specification
- The FreshRSS community for API documentation