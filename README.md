# Stremio MCP Server

MCP server for the Stremio addon protocol. Enables AI assistants (Claude Desktop, Claude Code, …) to search for movies and TV shows, read metadata, and query any Stremio addon.

## Tools

### Public (no login required)

| Tool | Description |
|---|---|
| `stremio_search` | Search movies/series by name |
| `stremio_get_meta` | Metadata for a title by IMDb ID (description, genres, cast, director, episode list) |
| `stremio_browse_catalog` | Browse catalogs – popular titles, filter by genre or year |
| `stremio_get_addon_manifest` | Fetch the manifest of any addon |
| `stremio_get_streams` | Get stream list for a title from any addon |

### User account & library (requires login)

| Tool | Description |
|---|---|
| `stremio_login` | Log in with email and password |
| `stremio_get_library` | Fetch the user's library |
| `stremio_add_to_library` | Add a movie or series to the library |
| `stremio_remove_from_library` | Remove a title from the library |

## Installation

Requires Python 3.10+.

**macOS**

```bash
git clone https://github.com/yourusername/stremio-mcp.git
cd stremio-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows**

```cmd
git clone https://github.com/yourusername/stremio-mcp.git
cd stremio-mcp
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

## Claude Desktop setup (global)

Open (or create) `claude_desktop_config.json` and add the `stremio` entry. Use the **absolute path** to the Python binary inside `.venv` and to `stremio_mcp.py`.

Config file location:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

**macOS**

```json
{
  "mcpServers": {
    "stremio": {
      "command": "/absolute/path/to/stremio-mcp/.venv/bin/python3",
      "args": ["/absolute/path/to/stremio-mcp/stremio_mcp.py"]
    }
  }
}
```

**Windows**

```json
{
  "mcpServers": {
    "stremio": {
      "command": "C:\\absolute\\path\\to\\stremio-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\absolute\\path\\to\\stremio-mcp\\stremio_mcp.py"]
    }
  }
}
```

Restart Claude Desktop after saving.

## Claude Code setup (per-project)

Create `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "stremio": {
      "command": "/absolute/path/to/stremio-mcp/.venv/bin/python3",
      "args": ["/absolute/path/to/stremio-mcp/stremio_mcp.py"]
    }
  }
}
```

> `.mcp.json` is listed in `.gitignore` so credentials (if added) are never committed.

## Authentication

Tools that access the user library require a Stremio account. Three ways to provide credentials:

**Option A – log in via the tool** (simplest):
Call `stremio_login` with your email and password. The session persists for the lifetime of the server process.

**Option B – environment variable**:
```bash
STREMIO_AUTH_KEY=your_key python stremio_mcp.py
```
Obtain the key by calling `stremio_login` once and copying `auth_key` from the response.

**Option C – credentials in config** (recommended – logs in automatically on startup):

Add an `env` block to either `claude_desktop_config.json` or `.mcp.json`:

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

## Notes

- Titles are identified by IMDb ID (e.g. `tt1375666`). Both `stremio_search` and catalog tools return it.
- Cinemeta provides metadata only, **not streams** – for `stremio_get_streams` supply the URL of an addon that supports the `stream` resource.
- The server accepts `stremio://` deep-link URLs – they are converted to `https://` automatically.
- Read-only tools (search, metadata, catalogs) do not require authentication.
