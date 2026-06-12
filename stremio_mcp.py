"""Stremio MCP server.

MCP server pro praci se Stremio addon protokolem:
- vyhledavani filmu a serialu pres oficialni Cinemeta addon
- detailni metadata podle IMDb ID
- prochazeni katalogu (top/popular, filtrovani podle zanru)
- nacteni manifestu libovolneho addonu
- ziskani streamu z libovolneho addonu
- sprava uzivatelske knihovny (prihlaseni, oblibene, pridavani/odebirani)

Spusteni: python stremio_mcp.py  (stdio transport)
"""

import json
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Konstanty
# ---------------------------------------------------------------------------

CINEMETA_BASE = "https://v3-cinemeta.strem.io"
STREMIO_API_BASE = "https://api.strem.io"
REQUEST_TIMEOUT = 20.0
USER_AGENT = "stremio-mcp/1.0"

mcp = FastMCP("stremio_mcp")

# Auth key sdileny v ramci procesu; inicializuje se z env promenne.
_auth_key: str | None = os.environ.get("STREMIO_AUTH_KEY")


# ---------------------------------------------------------------------------
# Sdilene pomocne funkce
# ---------------------------------------------------------------------------

async def _fetch_json(url: str) -> dict[str, Any]:
    """Fetch a URL and return parsed JSON, raising httpx errors on failure."""
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _api_post(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """POST to api.strem.io/api/{method} and return the result field."""
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    ) as client:
        resp = await client.post(f"{STREMIO_API_BASE}/api/{method}", json=params)
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise ValueError(body["error"])
        result = body.get("result")
        if result is None:
            raise ValueError(f"API returned no result for method '{method}'")
        return result


def _require_auth() -> str:
    """Return the current auth key or raise ValueError."""
    if not _auth_key:
        raise ValueError(
            "Not authenticated. Call stremio_login first, or set the "
            "STREMIO_AUTH_KEY environment variable before starting the server."
        )
    return _auth_key


def _handle_error(e: Exception, context: str) -> str:
    """Format exceptions into clear, actionable error messages."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 401:
            return (
                f"Error: {context} – authentication failed (HTTP 401). "
                "Your auth key may be expired. Call stremio_login again."
            )
        if code == 404:
            return (
                f"Error: {context} not found (HTTP 404). "
                "Check that the ID/URL is correct (e.g. IMDb ID like 'tt1375666', "
                "type 'movie' or 'series')."
            )
        return f"Error: {context} request failed with HTTP {code}."
    if isinstance(e, httpx.TimeoutException):
        return f"Error: {context} request timed out. The addon server may be slow; try again."
    if isinstance(e, (httpx.ConnectError, httpx.RequestError)):
        return f"Error: could not connect for {context}. Check the URL and your network."
    if isinstance(e, json.JSONDecodeError):
        return f"Error: {context} returned invalid JSON (probably not a Stremio addon endpoint)."
    if isinstance(e, ValueError):
        return f"Error: {e}"
    return f"Error: unexpected {type(e).__name__} during {context}: {e}"


def _normalize_addon_url(url: str) -> str:
    """Normalize an addon URL to its base (strip trailing /manifest.json and slashes)."""
    url = url.strip().rstrip("/")
    if url.endswith("/manifest.json"):
        url = url[: -len("/manifest.json")]
    # Stremio deep links use the stremio:// scheme; convert to https.
    if url.startswith("stremio://"):
        url = "https://" + url[len("stremio://"):]
    return url


def _slim_meta(meta: dict[str, Any], full: bool = False) -> dict[str, Any]:
    """Reduce a Cinemeta meta object to the fields agents actually need."""
    base_fields = ["id", "imdb_id", "type", "name", "year", "releaseInfo",
                   "imdbRating", "genres", "description"]
    extra_fields = ["runtime", "director", "cast", "writer", "country",
                    "awards", "poster", "background", "logo", "trailers",
                    "released", "language", "status"]
    fields = base_fields + extra_fields if full else base_fields
    slim = {k: meta[k] for k in fields if meta.get(k) is not None}
    # For series, summarize episodes instead of dumping hundreds of objects.
    if full and isinstance(meta.get("videos"), list):
        videos = meta["videos"]
        seasons: dict[int, int] = {}
        for v in videos:
            s = v.get("season")
            if isinstance(s, int):
                seasons[s] = seasons.get(s, 0) + 1
        slim["episodes_summary"] = {
            "total_videos": len(videos),
            "episodes_per_season": dict(sorted(seasons.items())),
        }
    return slim


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _slim_library_item(item: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw library item to the fields most useful for agents."""
    keep = ["_id", "name", "type", "year", "imdbRating", "genres",
            "poster", "removed", "temp", "_ctime", "_mtime"]
    slim = {k: item[k] for k in keep if item.get(k) is not None}
    state = item.get("state", {})
    if state.get("timesWatched"):
        slim["timesWatched"] = state["timesWatched"]
    if state.get("lastWatched"):
        slim["lastWatched"] = state["lastWatched"]
    return slim


def _format_results(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Vstupni modely
# ---------------------------------------------------------------------------

class ContentType(str, Enum):
    """Stremio content type."""
    MOVIE = "movie"
    SERIES = "series"


class SearchInput(BaseModel):
    """Input for searching the Cinemeta catalog."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description="Search query, e.g. 'inception' or 'breaking bad'",
                       min_length=1, max_length=200)
    content_type: ContentType = Field(
        default=ContentType.MOVIE,
        description="What to search for: 'movie' or 'series'",
    )
    limit: int = Field(default=10, description="Maximum number of results to return", ge=1, le=50)


class MetaInput(BaseModel):
    """Input for fetching detailed metadata of a title."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    imdb_id: str = Field(..., description="IMDb ID of the title, e.g. 'tt1375666'",
                         pattern=r"^tt\d{6,10}$")
    content_type: ContentType = Field(
        default=ContentType.MOVIE,
        description="Type of the title: 'movie' or 'series'",
    )


class CatalogInput(BaseModel):
    """Input for browsing Cinemeta catalogs."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    content_type: ContentType = Field(
        default=ContentType.MOVIE,
        description="Catalog content type: 'movie' or 'series'",
    )
    catalog_id: str = Field(
        default="top",
        description="Catalog ID. Cinemeta provides 'top' (popular) and 'year' (by release year).",
        min_length=1, max_length=50,
    )
    genre: Optional[str] = Field(
        default=None,
        description="Optional genre filter, e.g. 'Action', 'Comedy', 'Drama', 'Sci-Fi'. "
                    "For the 'year' catalog pass a year like '2024'.",
        max_length=50,
    )
    skip: int = Field(default=0, description="Number of items to skip (pagination)", ge=0)
    limit: int = Field(default=20, description="Maximum number of results to return", ge=1, le=100)


class ManifestInput(BaseModel):
    """Input for fetching an addon manifest."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    addon_url: str = Field(
        ...,
        description="Base URL of the Stremio addon, with or without trailing /manifest.json. "
                    "Example: 'https://v3-cinemeta.strem.io'",
        min_length=8, max_length=500,
    )

    @field_validator("addon_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://") or v.startswith("stremio://")):
            raise ValueError("addon_url must start with http://, https:// or stremio://")
        return v


class StreamsInput(ManifestInput):
    """Input for fetching streams for a title from a given addon."""
    imdb_id: str = Field(..., description="IMDb ID of the title, e.g. 'tt1375666'",
                         pattern=r"^tt\d{6,10}$")
    content_type: ContentType = Field(
        default=ContentType.MOVIE,
        description="Type of the title: 'movie' or 'series'",
    )
    season: Optional[int] = Field(
        default=None, description="Season number (required for series)", ge=0, le=200)
    episode: Optional[int] = Field(
        default=None, description="Episode number (required for series)", ge=0, le=2000)


class LoginInput(BaseModel):
    """Input for logging into a Stremio account."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(..., description="Stremio account e-mail address")
    password: str = Field(..., description="Stremio account password", min_length=1)


class LibraryQueryInput(BaseModel):
    """Input for querying the user library."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    content_type: Optional[ContentType] = Field(
        default=None,
        description="Filter by type: 'movie' or 'series'. Omit to return both.",
    )
    include_removed: bool = Field(
        default=False,
        description="Include titles that were removed from the library.",
    )
    limit: int = Field(default=50, description="Maximum number of items to return", ge=1, le=500)


class LibraryItemInput(BaseModel):
    """Input identifying a single title for library operations."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    imdb_id: str = Field(..., description="IMDb ID of the title, e.g. 'tt1375666'",
                         pattern=r"^tt\d{6,10}$")
    content_type: ContentType = Field(
        default=ContentType.MOVIE,
        description="Type of the title: 'movie' or 'series'",
    )


# ---------------------------------------------------------------------------
# Tools – addon protocol (beze zmeny)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="stremio_search",
    annotations={
        "title": "Search movies/series (Cinemeta)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def stremio_search(params: SearchInput) -> str:
    """Search for movies or series by name using Stremio's official Cinemeta addon.

    Returns a JSON object:
        {
          "query": str,
          "content_type": "movie" | "series",
          "count": int,
          "results": [{"id", "name", "year"/"releaseInfo", "imdbRating", "genres", ...}]
        }

    The "id" field is an IMDb ID (e.g. "tt1375666") usable with stremio_get_meta
    and stremio_get_streams.
    """
    url = f"{CINEMETA_BASE}/catalog/{params.content_type.value}/top/search={params.query}.json"
    try:
        data = await _fetch_json(url)
    except Exception as e:
        return _handle_error(e, "Cinemeta search")

    metas = data.get("metas", [])[: params.limit]
    results = [_slim_meta(m) for m in metas]
    return _format_results({
        "query": params.query,
        "content_type": params.content_type.value,
        "count": len(results),
        "results": results,
    })


@mcp.tool(
    name="stremio_get_meta",
    annotations={
        "title": "Get title metadata (Cinemeta)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def stremio_get_meta(params: MetaInput) -> str:
    """Get detailed metadata for a movie or series by IMDb ID from Cinemeta.

    Returns a JSON object with name, year, description, genres, cast, director,
    runtime, rating, poster/background URLs and (for series) an episode summary
    per season.
    """
    url = f"{CINEMETA_BASE}/meta/{params.content_type.value}/{params.imdb_id}.json"
    try:
        data = await _fetch_json(url)
    except Exception as e:
        return _handle_error(e, f"metadata for {params.imdb_id}")

    meta = data.get("meta")
    if not meta:
        return (
            f"Error: no metadata found for {params.imdb_id} as type "
            f"'{params.content_type.value}'. If it is a TV show, retry with "
            "content_type='series' (or 'movie' for films)."
        )
    return _format_results(_slim_meta(meta, full=True))


@mcp.tool(
    name="stremio_browse_catalog",
    annotations={
        "title": "Browse Cinemeta catalog",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def stremio_browse_catalog(params: CatalogInput) -> str:
    """Browse Cinemeta catalogs of popular movies/series, optionally filtered by genre.

    Catalogs: 'top' (popular titles, supports genre filter like 'Action', 'Drama'),
    'year' (by release year, pass the year in the genre field, e.g. '2024').

    Returns JSON with pagination info:
        {"count", "skip", "has_more", "next_skip", "results": [...]}
    """
    extras = []
    if params.genre:
        extras.append(f"genre={params.genre}")
    if params.skip:
        extras.append(f"skip={params.skip}")
    extra_path = f"/{'&'.join(extras)}" if extras else ""
    url = (f"{CINEMETA_BASE}/catalog/{params.content_type.value}/"
           f"{params.catalog_id}{extra_path}.json")
    try:
        data = await _fetch_json(url)
    except Exception as e:
        return _handle_error(e, f"catalog '{params.catalog_id}'")

    metas = data.get("metas", [])
    results = [_slim_meta(m) for m in metas[: params.limit]]
    has_more = len(metas) > params.limit or len(metas) >= 100
    return _format_results({
        "catalog_id": params.catalog_id,
        "content_type": params.content_type.value,
        "genre": params.genre,
        "skip": params.skip,
        "count": len(results),
        "has_more": has_more,
        "next_skip": params.skip + len(results) if has_more else None,
        "results": results,
    })


@mcp.tool(
    name="stremio_get_addon_manifest",
    annotations={
        "title": "Get addon manifest",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def stremio_get_addon_manifest(params: ManifestInput) -> str:
    """Fetch the manifest.json of any Stremio addon to discover its capabilities.

    Returns JSON with the addon's id, name, version, description, supported
    resources (catalog/meta/stream/subtitles), content types, catalogs and
    ID prefixes it understands.
    """
    base = _normalize_addon_url(params.addon_url)
    try:
        manifest = await _fetch_json(f"{base}/manifest.json")
    except Exception as e:
        return _handle_error(e, f"manifest at {base}")

    keep = ["id", "name", "version", "description", "resources", "types",
            "idPrefixes", "catalogs", "behaviorHints"]
    slim = {k: manifest[k] for k in keep if k in manifest}
    if isinstance(slim.get("catalogs"), list):
        slim["catalogs"] = [
            {k: c.get(k) for k in ("type", "id", "name") if c.get(k) is not None}
            for c in slim["catalogs"]
        ]
    slim["addon_base_url"] = base
    return _format_results(slim)


@mcp.tool(
    name="stremio_get_streams",
    annotations={
        "title": "Get streams from an addon",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def stremio_get_streams(params: StreamsInput) -> str:
    """Query a Stremio addon for available streams for a given title.

    For series, both season and episode must be provided (the video ID is
    built as 'ttXXXXXXX:season:episode').

    Returns JSON: {"video_id", "addon": str, "count": int, "streams": [
        {"name", "title", "url", "infoHash", "behaviorHints", ...}]}

    Note: Cinemeta itself does not provide streams; use a stream-capable addon
    URL (check its manifest 'resources' for 'stream' first).
    """
    base = _normalize_addon_url(params.addon_url)
    video_id = params.imdb_id
    if params.content_type == ContentType.SERIES:
        if params.season is None or params.episode is None:
            return ("Error: for series you must provide both 'season' and 'episode' "
                    "(e.g. season=1, episode=3).")
        video_id = f"{params.imdb_id}:{params.season}:{params.episode}"

    url = f"{base}/stream/{params.content_type.value}/{video_id}.json"
    try:
        data = await _fetch_json(url)
    except Exception as e:
        return _handle_error(e, f"streams for {video_id}")

    streams = data.get("streams", [])
    slim_streams = []
    for s in streams[:30]:
        slim_streams.append({k: s.get(k) for k in
                             ("name", "title", "description", "url", "ytId",
                              "infoHash", "fileIdx", "behaviorHints")
                             if s.get(k) is not None})
    return _format_results({
        "video_id": video_id,
        "addon": base,
        "count": len(slim_streams),
        "total_available": len(streams),
        "streams": slim_streams,
    })


# ---------------------------------------------------------------------------
# Tools – uzivatelsky ucet a knihovna
# ---------------------------------------------------------------------------

@mcp.tool(
    name="stremio_login",
    annotations={
        "title": "Log in to Stremio account",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def stremio_login(params: LoginInput) -> str:
    """Authenticate with a Stremio account using email and password.

    On success stores the auth key for the lifetime of this server process
    so that stremio_get_library, stremio_add_to_library and
    stremio_remove_from_library can use it without re-authenticating.

    Returns JSON: {"user_id", "email", "auth_key"}
    """
    global _auth_key
    try:
        result = await _api_post("login", {
            "email": params.email,
            "password": params.password,
            "facebook": False,
        })
    except Exception as e:
        return _handle_error(e, "Stremio login")

    _auth_key = result.get("authKey")
    if not _auth_key:
        return "Error: login succeeded but no authKey returned. Check your credentials."

    user = result.get("user", {})
    return _format_results({
        "status": "authenticated",
        "user_id": user.get("_id"),
        "email": user.get("email"),
        "auth_key": _auth_key,
    })


@mcp.tool(
    name="stremio_get_library",
    annotations={
        "title": "Get user library",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def stremio_get_library(params: LibraryQueryInput) -> str:
    """Fetch the authenticated user's Stremio library (saved/favourite titles).

    Requires prior call to stremio_login (or STREMIO_AUTH_KEY env var).

    Returns JSON: {"count", "items": [{"_id", "name", "type", "removed",
    "timesWatched", "lastWatched", ...}]}
    """
    try:
        auth = _require_auth()
        result = await _api_post("datastoreGet", {
            "authKey": auth,
            "collection": "libraryItem",
            "ids": [],
            "all": True,
        })
    except Exception as e:
        return _handle_error(e, "library fetch")

    items: list[dict] = result if isinstance(result, list) else result.get("items", [])

    if not params.include_removed:
        items = [i for i in items if not i.get("removed")]
    if params.content_type:
        items = [i for i in items if i.get("type") == params.content_type.value]

    items = items[: params.limit]
    return _format_results({
        "count": len(items),
        "items": [_slim_library_item(i) for i in items],
    })


@mcp.tool(
    name="stremio_add_to_library",
    annotations={
        "title": "Add title to library",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def stremio_add_to_library(params: LibraryItemInput) -> str:
    """Add a movie or series to the authenticated user's Stremio library.

    Fetches title metadata (name, poster) from Cinemeta automatically –
    only the IMDb ID and content type are required.

    Requires prior call to stremio_login (or STREMIO_AUTH_KEY env var).

    Returns JSON: {"status", "item": {"_id", "name", "type"}}
    """
    try:
        auth = _require_auth()
    except ValueError as e:
        return _handle_error(e, "add to library")

    # Fetch name + poster from Cinemeta so we store a complete library item.
    meta_url = f"{CINEMETA_BASE}/meta/{params.content_type.value}/{params.imdb_id}.json"
    try:
        meta_data = await _fetch_json(meta_url)
    except Exception as e:
        return _handle_error(e, f"metadata for {params.imdb_id}")

    meta = meta_data.get("meta", {})
    name = meta.get("name") or params.imdb_id
    poster = meta.get("poster")
    now = _now_iso()

    library_item: dict[str, Any] = {
        "_id": params.imdb_id,
        "name": name,
        "type": params.content_type.value,
        "removed": False,
        "temp": False,
        "_ctime": now,
        "_mtime": now,
        "state": {
            "timeWatched": 0,
            "timeOffset": 0,
            "overallTimeWatched": 0,
            "timesWatched": 0,
            "flaggedWatched": 0,
            "duration": 0,
        },
        "behaviorHints": {},
    }
    if poster:
        library_item["poster"] = poster
        library_item["posterShape"] = "poster"

    try:
        await _api_post("datastorePut", {
            "authKey": auth,
            "collection": "libraryItem",
            "changes": [library_item],
        })
    except Exception as e:
        return _handle_error(e, f"add {params.imdb_id} to library")

    return _format_results({
        "status": "added",
        "item": {"_id": params.imdb_id, "name": name, "type": params.content_type.value},
    })


@mcp.tool(
    name="stremio_remove_from_library",
    annotations={
        "title": "Remove title from library",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def stremio_remove_from_library(params: LibraryItemInput) -> str:
    """Remove a movie or series from the authenticated user's Stremio library.

    The item is soft-deleted (marked removed=true) which matches Stremio's own
    behaviour and allows the history to be restored if needed.

    Requires prior call to stremio_login (or STREMIO_AUTH_KEY env var).

    Returns JSON: {"status", "_id"}
    """
    try:
        auth = _require_auth()
    except ValueError as e:
        return _handle_error(e, "remove from library")

    # Fetch the existing item to preserve its state fields.
    try:
        result = await _api_post("datastoreGet", {
            "authKey": auth,
            "collection": "libraryItem",
            "ids": [params.imdb_id],
            "all": False,
        })
    except Exception as e:
        return _handle_error(e, f"fetch existing library item {params.imdb_id}")

    existing_items: list[dict] = result if isinstance(result, list) else result.get("items", [])
    now = _now_iso()

    if existing_items:
        item = existing_items[0]
        item["removed"] = True
        item["_mtime"] = now
    else:
        # Item not in library yet; create a minimal tombstone so the removal is synced.
        item = {
            "_id": params.imdb_id,
            "name": params.imdb_id,
            "type": params.content_type.value,
            "removed": True,
            "temp": False,
            "_ctime": now,
            "_mtime": now,
            "state": {
                "timeWatched": 0, "timeOffset": 0, "overallTimeWatched": 0,
                "timesWatched": 0, "flaggedWatched": 0, "duration": 0,
            },
            "behaviorHints": {},
        }

    try:
        await _api_post("datastorePut", {
            "authKey": auth,
            "collection": "libraryItem",
            "changes": [item],
        })
    except Exception as e:
        return _handle_error(e, f"remove {params.imdb_id} from library")

    return _format_results({"status": "removed", "_id": params.imdb_id})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
