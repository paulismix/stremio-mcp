# Stremio MCP Server

MCP server for working with the Stremio addon protocol. Enables AI assistants (Claude Desktop, Claude Code, …) to search for movies and TV shows, read metadata, and query any Stremio addon.

## Tools

### Addon protocol (public, no login required)

| Tool | Description |
|---|---|
| `stremio_search` | Search movies/series by name (via the official Cinemeta addon) |
| `stremio_get_meta` | Detailed metadata for a title by IMDb ID (description, genres, cast, director; episode list for series) |
| `stremio_browse_catalog` | Browse catalogs – popular titles, filter by genre or year, pagination |
| `stremio_get_addon_manifest` | Fetch the manifest of any addon (capabilities, catalogs, supported types) |
| `stremio_get_streams` | Get stream list for a title from any addon (for series provide `season` + `episode`) |

### User account & library (requires login)

| Tool | Description |
|---|---|
| `stremio_login` | Log in with email and password; stores `authKey` for the lifetime of the server process |
| `stremio_get_library` | Fetch the authenticated user's library (filter by type, pagination) |
| `stremio_add_to_library` | Add a movie or series to the library (metadata fetched automatically from Cinemeta) |
| `stremio_remove_from_library` | Remove a title from the library (soft-delete, same behaviour as native Stremio) |

## Installation

```bash
pip install -r requirements.txt
```

(Requires Python 3.10+.)

## Quick test

```bash
python stremio_mcp.py
```

The server runs over stdio – after startup it waits for an MCP client. For interactive testing use MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python stremio_mcp.py
```

## Claude Desktop integration

Add the following to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "stremio": {
      "command": "python",
      "args": ["/absolute/path/to/stremio_mcp.py"]
    }
  }
}
```

Config file location:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

Restart Claude Desktop after saving – the tools will appear automatically.

## Authentication

Three options for tools that require a logged-in user:

**Option A – log in at runtime** (recommended for interactive use):
Call the `stremio_login` tool with your email and password. The `authKey` is stored in server memory for the duration of the process.

**Option B – pre-obtained auth key** (recommended for automation):
Set `STREMIO_AUTH_KEY` before starting the server:
```bash
STREMIO_AUTH_KEY=your_key python stremio_mcp.py
```
You can obtain the key by calling `stremio_login` once and copying the `auth_key` field from the response.

**Option C – credentials in `.mcp.json`** (recommended for Claude Code projects):
Store your email and password as environment variables directly in `.mcp.json`. The server reads them on startup and logs in automatically – no manual `stremio_login` call needed.

```json
{
  "mcpServers": {
    "stremio": {
      "command": "/absolute/path/to/.venv/bin/python3",
      "args": ["/absolute/path/to/stremio_mcp.py"],
      "env": {
        "STREMIO_EMAIL": "your@email.com",
        "STREMIO_PASSWORD": "yourpassword"
      }
    }
  }
}
```

> `.mcp.json` is listed in `.gitignore` so credentials are never committed to the repository.

## Usage examples (prompts for the assistant)

- "Find the movie Inception and tell me about it."
- "What are the popular sci-fi series right now?"
- "Load the manifest from https://example.com/manifest.json and check if it supports streams."
- "Get streams for Breaking Bad S02E05 from addon X."

## Notes

- Titles are identified by IMDb ID (e.g. `tt1375666`). Both `stremio_search` and catalog tools return it.
- Cinemeta provides metadata only, **not streams** – for `stremio_get_streams` you must supply a URL of an addon that supports the `stream` resource (verify via `stremio_get_addon_manifest`).
- The server also accepts `stremio://` deep-link URLs – they are converted to `https://` automatically.
- Read-only tools (search, metadata, catalogs) do not require any authentication.
