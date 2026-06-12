"""Stremio MCP server.

MCP server pro praci se Stremio addon protokolem:
- vyhledavani filmu a serialu pres oficialni Cinemeta addon
- detailni metadata podle IMDb ID
- prochazeni katalogu (top/popular, filtrovani podle zanru)
- nacteni manifestu libovolneho addonu
- ziskani streamu z libovolneho addonu

Spusteni: python stremio_mcp.py  (stdio transport)
"""

import json
from enum import Enum
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Konstanty
# ---------------------------------------------------------------------------

CINEMETA_BASE = "https://v3-cinemeta.strem.io"
REQUEST_TIMEOUT = 20.0
USER_AGENT = "stremio-mcp/1.0"

mcp = FastMCP("stremio_mcp")


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


def _handle_error(e: Exception, context: str) -> str:
    """Format exceptions into clear, actionable error messages."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
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


# ---------------------------------------------------------------------------
# Tools
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
