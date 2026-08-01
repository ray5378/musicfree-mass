"""
MusicFree Plugin for Music Assistant.

OpenSubsonic API bridge for MusicFree clients - browse and stream all MA music
sources (local files, Spotify, Tidal, etc.) via the Subsonic protocol, compatible
with the music-free-site project.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aiohttp import web
from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType, MediaType
from music_assistant_models.errors import MediaNotFoundError
from music_assistant_models.media_items import (
    Album,
    Artist,
    Playlist,
    Track,
)

from music_assistant.helpers.images import get_image_data
from music_assistant.models import ProviderInstanceType
from music_assistant.models.music_provider import MusicProvider

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from music_assistant_models.config_entries import ConfigValueType, ProviderConfig
    from music_assistant_models.enums import ProviderFeature
    from music_assistant_models.media_items import SearchResults
    from music_assistant_models.provider import ProviderManifest
    from music_assistant_models.streamdetails import StreamDetails

    from music_assistant.mass import MusicAssistant

DOMAIN = "musicfree"
SUBSONIC_VERSION = "1.16.1"
SERVER_VERSION = "0.1.1"
CONF_PORT = "port"
CONF_TOKEN = "token"
CONF_SEARCH_SCOPE = "search_scope"
LIBRARY_MAX = 99999

SUPPORTED_FEATURES: set[ProviderFeature] = set()

# Map of Subsonic endpoint names to handler methods
ENDPOINT_MAP: dict[str, str] = {
    "ping": "_handle_ping",
    "getLicense": "_handle_get_license",
    "getScanStatus": "_handle_get_scan_status",
    "getMusicFolders": "_handle_get_music_folders",
    "getIndexes": "_handle_get_indexes",
    "getArtists": "_handle_get_artists",
    "getArtist": "_handle_get_artist",
    "getAlbum": "_handle_get_album",
    "getSong": "_handle_get_song",
    "getMusicDirectory": "_handle_get_music_directory",
    "getAlbumList": "_handle_get_album_list",
    "getAlbumList2": "_handle_get_album_list",
    "search2": "_handle_search",
    "search3": "_handle_search",
    "getPlaylists": "_handle_get_playlists",
    "getPlaylist": "_handle_get_playlist",
    "getCoverArt": "_handle_get_cover_art",
    "stream": "_handle_stream",
    "download": "_handle_stream",
    "scrobble": "_handle_scrobble",
    "getNowPlaying": "_handle_get_now_playing",
    "getStarred": "_handle_get_starred",
    "getStarred2": "_handle_get_starred",
    "star": "_handle_star",
    "unstar": "_handle_unstar",
    "getUser": "_handle_get_user",
    "getArtistInfo": "_handle_get_artist_info",
    "getArtistInfo2": "_handle_get_artist_info",
    "getSimilarSongs": "_handle_get_similar_songs",
    "getSimilarSongs2": "_handle_get_similar_songs",
    "getLyricsBySongId": "_handle_get_lyrics",
    "getOpenSubsonicExtensions": "_handle_get_extensions",
    "getRandomSongs": "_handle_get_random_songs",
    "createPlaylist": "_handle_create_playlist",
    "updatePlaylist": "_handle_update_playlist",
    "deletePlaylist": "_handle_delete_playlist",
    "getPlaylistCoverArt": "_handle_get_cover_art",
}

logger = logging.getLogger(__name__)


def _xml_bool(value: bool) -> str:
    return "true" if value else "false"


def _build_subsonic_response() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": SUBSONIC_VERSION,
        "serverVersion": SERVER_VERSION,
        "openSubsonic": True,
        "type": "MusicFree",
    }


def _json_response(data: dict[str, Any]) -> web.Response:
    return web.json_response({"subsonic-response": data})


def _xml_response(root: dict[str, Any]) -> web.Response:
    xml_str = _dict_to_xml("subsonic-response", root)
    return web.Response(text=xml_str, content_type="application/xml")


def _dict_to_xml(tag: str, data: Any) -> str:
    """Convert a dict/list structure to XML string."""
    elem = _build_xml_element(tag, data)
    return ET.tostring(elem, encoding="unicode", xml_declaration=True)


def _build_xml_element(tag: str, data: Any) -> ET.Element:
    """Build an XML element from Python data."""
    elem = ET.Element(tag)

    if isinstance(data, dict):
        if "@" in data:
            # Attributes-only element
            for key, value in data["@"].items():
                if value is not None:
                    elem.set(key, str(value))
            return elem
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = _xml_bool(value)
            child = _build_xml_element(key, value)
            elem.append(child)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "list_tag" in item:
                child_tag = item["list_tag"]
                inner = {k: v for k, v in item.items() if k != "list_tag"}
                child = _build_xml_element(child_tag, inner)
                elem.append(child)
            else:
                child = _build_xml_element("item", item)
                elem.append(child)
    else:
        elem.text = str(data)
    return elem


def _response(
    data: dict[str, Any], fmt: str = "json"
) -> web.Response:
    """Return response in the requested format."""
    resp = _build_subsonic_response()
    resp.update(data)
    if fmt == "json":
        return _json_response(resp)
    return _xml_response(resp)


def _error_response(
    code: int, message: str, fmt: str = "json"
) -> web.Response:
    """Return an error response."""
    resp = _build_subsonic_response()
    resp["status"] = "failed"
    resp["error"] = {"code": code, "message": message}
    if fmt == "json":
        return _json_response(resp)
    return _xml_response(resp)


def _get_format(request: web.Request) -> str:
    """Get the response format from the request."""
    params = dict(request.query)
    return params.get("f", "json").lower()


def _make_cover_art_id(item_type: str, item_id: str) -> str:
    """Create a cover art ID string."""
    prefix = {"album": "al", "artist": "ar", "playlist": "pl", "track": "tr"}.get(item_type, "xx")
    return f"{prefix}-{item_id}"


def _parse_cover_art_id(cover_art_id: str) -> tuple[str, str] | None:
    """Parse a cover art ID into (type, id)."""
    prefixes = {"al": "album", "ar": "artist", "pl": "playlist", "tr": "track"}
    for prefix, item_type in prefixes.items():
        if cover_art_id.startswith(f"{prefix}-"):
            return item_type, cover_art_id[len(prefix) + 1:]
    return None


def _artist_to_subsonic(
    artist: Artist, instance_id: str, domain: str
) -> dict[str, Any]:
    """Convert MA Artist to Subsonic artist dict."""
    mapping = next(iter(artist.provider_mappings), None)
    album_count = 0
    try:
        album_count = len(artist.albums) if hasattr(artist, "albums") and artist.albums else 0
    except Exception:
        pass
    cover_id = _make_cover_art_id("artist", artist.item_id)
    return {
        "id": artist.item_id,
        "name": artist.name,
        "coverArt": cover_id,
        "albumCount": album_count,
    }


def _album_to_subsonic(
    album: Album, instance_id: str, domain: str
) -> dict[str, Any]:
    """Convert MA Album to Subsonic album dict."""
    mapping = next(iter(album.provider_mappings), None)
    cover_id = _make_cover_art_id("album", album.item_id)
    artist_name = album.artist.name if album.artist else "Unknown Artist"
    artist_id = album.artist.item_id if album.artist else "unknown"
    result = {
        "id": album.item_id,
        "name": album.name,
        "artist": artist_name,
        "artistId": artist_id,
        "coverArt": cover_id,
        "songCount": album.track_count or 0,
        "duration": album.duration or 0,
        "created": _dt_to_rfc(album.metadata.created_at) if album.metadata and album.metadata.created_at else "",
        "year": album.year or 0,
        "genre": album.metadata.genres[0] if album.metadata and album.metadata.genres else "",
    }
    if album.metadata and album.metadata.musicbrainz_id:
        result["musicBrainzId"] = album.metadata.musicbrainz_id
    return result


def _track_to_subsonic(
    track: Track, instance_id: str, domain: str, album: Album | None = None
) -> dict[str, Any]:
    """Convert MA Track to Subsonic song dict."""
    artist_name = track.artist.name if track.artist else "Unknown Artist"
    artist_id = track.artist.item_id if track.artist else "unknown"
    album_name = track.album.name if track.album else (album.name if album else "Unknown Album")
    album_id = track.album.item_id if track.album else (album.item_id if album else "unknown")
    cover_id = _make_cover_art_id("album", album_id)
    result = {
        "id": track.item_id,
        "parent": album_id,
        "title": track.name,
        "artist": artist_name,
        "artistId": artist_id,
        "album": album_name,
        "albumId": album_id,
        "coverArt": cover_id,
        "duration": track.duration or 0,
        "track": track.track_number or 0,
        "year": track.year or 0,
        "genre": track.metadata.genres[0] if track.metadata and track.metadata.genres else "",
        "size": 0,
        "contentType": "audio/mpeg",
        "suffix": "mp3",
        "bitRate": track.metadata.bit_rate or 0 if track.metadata else 0,
        "path": track.item_id,
    }
    if track.metadata and track.metadata.disc_number:
        result["discNumber"] = track.metadata.disc_number
    return result


def _playlist_to_subsonic(
    playlist: Playlist, instance_id: str, domain: str
) -> dict[str, Any]:
    """Convert MA Playlist to Subsonic playlist dict."""
    cover_id = _make_cover_art_id("playlist", playlist.item_id)
    return {
        "id": playlist.item_id,
        "name": playlist.name,
        "owner": "admin",
        "public": False,
        "songCount": len(playlist.tracks) if hasattr(playlist, "tracks") and playlist.tracks else 0,
        "duration": playlist.duration or 0,
        "created": _dt_to_rfc(playlist.metadata.created_at) if playlist.metadata and playlist.metadata.created_at else "",
        "coverArt": cover_id,
    }


def _dt_to_rfc(dt: datetime | None) -> str:
    """Convert datetime to RFC 3339 string."""
    if dt is None:
        return ""
    return dt.isoformat()


class MusicFreeProvider(MusicProvider):
    """MusicFree Subsonic API bridge provider."""

    _server: web.TCPSite | None = None
    _app: web.Application | None = None
    _runner: web.AppRunner | None = None
    _token: str = ""
    _port: int = 4533
    _search_scope: list[str] | None = None

    @property
    def is_streaming_provider(self) -> bool:
        """Return True if the provider is a streaming provider."""
        return True

    async def search(
        self,
        search_query: str,
        media_types: list[MediaType],
        limit: int = 5,
    ) -> SearchResults:
        """Search for media items - always return empty (this provider only bridges)."""
        from music_assistant_models.media_items import SearchResults

        return SearchResults()

    async def get_library_artists(self) -> AsyncGenerator[Artist]:
        """Retrieve library artists - always empty (this provider only bridges)."""
        yield  # type: ignore[misc]
        return

    async def get_library_albums(self) -> AsyncGenerator[Album]:
        """Retrieve library albums - always empty (this provider only bridges)."""
        yield  # type: ignore[misc]
        return

    async def get_library_tracks(self) -> AsyncGenerator[Track]:
        """Retrieve library tracks - always empty (this provider only bridges)."""
        yield  # type: ignore[misc]
        return

    async def get_library_playlists(self) -> AsyncGenerator[Playlist]:
        """Retrieve library playlists - always empty (this provider only bridges)."""
        yield  # type: ignore[misc]
        return

    async def get_stream_details(
        self, item_id: str, media_type: MediaType = MediaType.TRACK
    ) -> StreamDetails:
        """Get stream details - not used for this bridge provider."""
        raise MediaNotFoundError(item_id)

    async def loaded_in_mass(self) -> None:
        """Start the HTTP server after loading."""
        self._port = int(self.get_setup_value(CONF_PORT) or 4533)
        self._token = str(self.get_setup_value(CONF_TOKEN) or "")
        scope = self.get_setup_value(CONF_SEARCH_SCOPE) or ""
        self._search_scope = [s.strip() for s in scope.split(",") if s.strip()] if scope else None

        if not self._token:
            self._token = secrets.token_hex(32)
            self.logger.warning("No token configured, generated: %s", self._token)

        self._app = web.Application()
        self._setup_routes()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._server = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await self._server.start()
        self.logger.info(
            "MusicFree Bridge server started on port %d (token: %s...)",
            self._port,
            self._token[:8],
        )

    async def unload(self, is_removed: bool = False) -> None:
        """Stop the HTTP server on unload."""
        if self._server:
            await self._server.stop()
            self._server = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None

    def _setup_routes(self) -> None:
        """Set up aiohttp routes."""
        assert self._app is not None
        self._app.router.add_get("/rest/{endpoint}", self._handle_request)
        self._app.router.add_post("/rest/{endpoint}", self._handle_request)
        self._app.router.add_get("/rest/{endpoint}.view", self._handle_request)
        self._app.router.add_post("/rest/{endpoint}.view", self._handle_request)
        self._app.router.add_get("/api/v1/artists/{artist_id}/avatar", self._handle_artist_avatar)

    # ------------------------------------------------------------------ #
    # Authentication                                                      #
    # ------------------------------------------------------------------ #

    async def _authenticate(self, request: web.Request) -> bool:
        """Authenticate the request against configured token."""
        params = dict(request.query)

        # X-API-Key header
        api_key = request.headers.get("X-API-Key", "")
        if api_key and api_key == self._token:
            return True

        # Authorization: Bearer <token>
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer_token = auth_header[7:]
            if bearer_token == self._token:
                return True

        # X-ND-Authorization: Bearer <token>
        nd_auth = request.headers.get("X-ND-Authorization", "")
        if nd_auth.startswith("Bearer "):
            nd_token = nd_auth[7:]
            if nd_token == self._token:
                return True

        # u+t+s (OpenSubsonic token auth) - accept any valid user/token/salt combo
        u = params.get("u", "")
        t = params.get("t", "")
        s = params.get("s", "")
        if u and t and s:
            # Try both md5(token + salt) and md5(md5(token) + salt)
            if t == hashlib.md5((self._token + s).encode()).hexdigest():
                return True
            if t == hashlib.md5(
                hashlib.md5(self._token.encode()).hexdigest().encode() + s.encode()
            ).hexdigest():
                return True

        # Allow ping without auth
        endpoint = request.match_info.get("endpoint", "").replace(".view", "")
        if endpoint == "ping":
            return True

        return False

    # ------------------------------------------------------------------ #
    # Request routing                                                      #
    # ------------------------------------------------------------------ #

    async def _handle_request(self, request: web.Request) -> web.Response:
        """Route incoming requests to the appropriate handler."""
        endpoint = request.match_info.get("endpoint", "").replace(".view", "")
        fmt = _get_format(request)

        # Authenticate
        if not await self._authenticate(request):
            return _error_response(40, "Authentication failed", fmt)

        # Find handler
        handler_name = ENDPOINT_MAP.get(endpoint)
        if handler_name is None:
            self.logger.warning("Unknown endpoint: %s", endpoint)
            return _error_response(70, f"Unknown endpoint: {endpoint}", fmt)

        handler = getattr(self, handler_name, None)
        if handler is None:
            return _error_response(70, f"Handler not implemented: {endpoint}", fmt)

        try:
            result = await handler(request)
            if isinstance(result, web.Response):
                return result
            return _response(result, fmt)
        except Exception as exc:
            self.logger.exception("Error handling endpoint %s: %s", endpoint, exc)
            return _error_response(0, str(exc), fmt)

    # ------------------------------------------------------------------ #
    # Endpoint handlers                                                    #
    # ------------------------------------------------------------------ #

    async def _handle_ping(self, request: web.Request) -> dict[str, Any]:
        """Ping the server."""
        return {}

    async def _handle_get_license(self, request: web.Request) -> dict[str, Any]:
        """Get license information."""
        return {"license": {"valid": True}}

    async def _handle_get_scan_status(self, request: web.Request) -> dict[str, Any]:
        """Get scan status."""
        return {"scanStatus": {"scanning": False, "count": 0}}

    async def _handle_get_music_folders(self, request: web.Request) -> dict[str, Any]:
        """Get music folders."""
        return {
            "musicFolders": {
                "musicFolder": [
                    {"id": "default-library", "name": "Default Library"}
                ]
            }
        }

    async def _handle_get_indexes(self, request: web.Request) -> dict[str, Any]:
        """Get indexes (alphabetical artist listing)."""
        artists = await self._get_all_artists()
        # Group by first letter
        index_map: dict[str, list[dict[str, Any]]] = {}
        for artist in artists:
            subsonic = _artist_to_subsonic(artist, self.instance_id, self.domain)
            name = artist.sort_name or artist.name
            first = name[0].upper() if name else "#"
            if first not in index_map:
                index_map[first] = []
            index_map[first].append(subsonic)

        index_list = []
        for letter in sorted(index_map.keys()):
            index_list.append({
                "name": letter,
                "artist": index_map[letter],
            })

        return {
            "indexes": {
                "lastModified": int(time.time() * 1000),
                "ignoredArticles": "The An A Die Das Ein Eine Les Le La",
                "index": index_list,
            }
        }

    async def _handle_get_artists(self, request: web.Request) -> dict[str, Any]:
        """Get all artists (same as getIndexes but with artists root)."""
        result = await self._handle_get_indexes(request)
        indexes = result.get("indexes", {})
        return {"artists": indexes}

    async def _handle_get_artist(self, request: web.Request) -> dict[str, Any]:
        """Get artist details."""
        params = dict(request.query)
        artist_id = params.get("id", "")
        try:
            artist = await self.mass.music.artists.get(artist_id, "library")
        except MediaNotFoundError:
            artist = None
        if artist is None:
            return {"error": {"code": 70, "message": "Artist not found"}}

        subsonic = _artist_to_subsonic(artist, self.instance_id, self.domain)

        # Get albums
        try:
            albums = await self.mass.music.artists.albums(artist.item_id, "library")
        except Exception:
            albums = []

        subsonic_albums = []
        for album in albums:
            subsonic_albums.append(_album_to_subsonic(album, self.instance_id, self.domain))

        subsonic["album"] = subsonic_albums
        return {"artist": subsonic}

    async def _handle_get_album(self, request: web.Request) -> dict[str, Any]:
        """Get album details."""
        params = dict(request.query)
        album_id = params.get("id", "")
        try:
            album = await self.mass.music.albums.get(album_id, "library")
        except MediaNotFoundError:
            album = None
        if album is None:
            return {"error": {"code": 70, "message": "Album not found"}}

        subsonic = _album_to_subsonic(album, self.instance_id, self.domain)

        # Get tracks
        try:
            tracks = await self.mass.music.albums.tracks(album.item_id, "library")
        except Exception:
            tracks = []

        subsonic["song"] = [
            _track_to_subsonic(t, self.instance_id, self.domain, album) for t in tracks
        ]

        return {"album": subsonic}

    async def _handle_get_song(self, request: web.Request) -> dict[str, Any]:
        """Get song details."""
        params = dict(request.query)
        song_id = params.get("id", "")
        try:
            track = await self.mass.music.tracks.get(song_id, "library")
        except MediaNotFoundError:
            return {"error": {"code": 70, "message": "Song not found"}}

        return {"song": [_track_to_subsonic(track, self.instance_id, self.domain)]}

    async def _handle_get_music_directory(self, request: web.Request) -> dict[str, Any]:
        """Get music directory contents."""
        params = dict(request.query)
        dir_id = params.get("id", "")

        # Try album first
        try:
            album = await self.mass.music.albums.get(dir_id, "library")
        except MediaNotFoundError:
            album = None

        if album:
            tracks = await self.mass.music.albums.tracks(album.item_id, "library")
            children = [
                _track_to_subsonic(t, self.instance_id, self.domain, album) for t in tracks
            ]
            return {
                "directory": {
                    "id": dir_id,
                    "name": album.name,
                    "child": children,
                }
            }

        # Try artist
        try:
            artist = await self.mass.music.artists.get(dir_id, "library")
        except MediaNotFoundError:
            artist = None

        if artist:
            albums = await self.mass.music.artists.albums(artist.item_id, "library")
            children = [
                {
                    "id": a.item_id,
                    "parent": dir_id,
                    "isDir": True,
                    "title": a.name,
                    "coverArt": _make_cover_art_id("album", a.item_id),
                    "songCount": a.track_count or 0,
                }
                for a in albums
            ]
            return {
                "directory": {
                    "id": dir_id,
                    "name": artist.name,
                    "child": children,
                }
            }

        # Return empty directory
        return {"directory": {"id": dir_id, "name": "Unknown", "child": []}}

    async def _handle_get_album_list(self, request: web.Request) -> dict[str, Any]:
        """Get album list (various types)."""
        params = dict(request.query)
        album_type = params.get("type", "newest")
        size = int(params.get("size", "50"))
        offset = int(params.get("offset", "0"))

        try:
            library_albums = []
            async for album in self.mass.music.albums.iter_library_items():
                library_albums.append(album)
        except Exception:
            library_albums = []

        # Sort based on type
        if album_type == "newest" or album_type == "recent":
            library_albums.sort(
                key=lambda a: (a.metadata.created_at or datetime.min) if a.metadata else datetime.min,
                reverse=True,
            )
        elif album_type == "alphabeticalByName":
            library_albums.sort(key=lambda a: a.sort_name or a.name or "")
        elif album_type == "alphabeticalByArtist":
            library_albums.sort(
                key=lambda a: a.artist.sort_name or a.artist.name or "" if a.artist else ""
            )
        elif album_type == "random":
            import random as rng

            rng.shuffle(library_albums)
        elif album_type == "byYear":
            year = int(params.get("fromYear", "0"))
            library_albums = [a for a in library_albums if a.year and a.year >= year]

        paginated = library_albums[offset: offset + size]
        subsonic_albums = [
            _album_to_subsonic(a, self.instance_id, self.domain) for a in paginated
        ]

        if params.get("type") == "random":
            return {"randomAlbums": {"album": subsonic_albums}}
        return {"albumList": {"album": subsonic_albums}}

    async def _handle_search(self, request: web.Request) -> dict[str, Any]:
        """Search for artists, albums, and songs."""
        params = dict(request.query)
        query = params.get("query", "")

        artist_count = int(params.get("artistCount", "20"))
        album_count = int(params.get("albumCount", "20"))
        song_count = int(params.get("songCount", "20"))

        artists: list[dict] = []
        albums: list[dict] = []
        songs: list[dict] = []

        if query:
            try:
                search_results = await self.mass.music.search(
                    search_query=query,
                    media_types=[MediaType.ARTIST, MediaType.ALBUM, MediaType.TRACK],
                    limit=max(artist_count, album_count, song_count),
                )
                for artist in search_results.artists[:artist_count]:
                    artists.append(_artist_to_subsonic(artist, self.instance_id, self.domain))
                for album in search_results.albums[:album_count]:
                    albums.append(_album_to_subsonic(album, self.instance_id, self.domain))
                for track in search_results.tracks[:song_count]:
                    songs.append(_track_to_subsonic(track, self.instance_id, self.domain))
            except Exception as exc:
                self.logger.warning("Search failed: %s", exc)

        result = {
            "artist": artists,
            "album": albums,
            "song": songs,
        }

        # Determine if this is search2 or search3
        endpoint = request.match_info.get("endpoint", "").replace(".view", "")
        if endpoint == "search3":
            return {"searchResult3": result}
        return {"searchResult2": result}

    async def _handle_get_playlists(self, request: web.Request) -> dict[str, Any]:
        """Get all playlists."""
        try:
            playlists = []
            async for pl in self.mass.music.playlists.iter_library_items():
                playlists.append(pl)
        except Exception:
            playlists = []

        subsonic_playlists = [
            _playlist_to_subsonic(pl, self.instance_id, self.domain) for pl in playlists
        ]
        return {"playlists": {"playlist": subsonic_playlists}}

    async def _handle_get_playlist(self, request: web.Request) -> dict[str, Any]:
        """Get playlist details."""
        params = dict(request.query)
        playlist_id = params.get("id", "")
        try:
            playlist = await self.mass.music.playlists.get(playlist_id, "library")
        except MediaNotFoundError:
            return {"error": {"code": 70, "message": "Playlist not found"}}

        subsonic = _playlist_to_subsonic(playlist, self.instance_id, self.domain)

        try:
            tracks = []
            async for t in self.mass.music.playlists.tracks(
                playlist.item_id, "library"
            ):
                if isinstance(t, Track):
                    tracks.append(t)
        except Exception:
            tracks = []

        subsonic["entry"] = [
            _track_to_subsonic(t, self.instance_id, self.domain) for t in tracks
        ]
        return {"playlist": subsonic}

    async def _handle_get_cover_art(self, request: web.Request) -> web.Response:
        """Get cover art image."""
        params = dict(request.query)
        cover_id = params.get("id", "")

        parsed = _parse_cover_art_id(cover_id)
        if parsed is None:
            item_type = "album"
            item_id = cover_id
        else:
            item_type, item_id = parsed

        media_item: Any = None
        try:
            if item_type == "album":
                media_item = await self.mass.music.albums.get(item_id, "library")
            elif item_type == "artist":
                media_item = await self.mass.music.artists.get(item_id, "library")
            elif item_type == "track":
                media_item = await self.mass.music.tracks.get(item_id, "library")
        except MediaNotFoundError:
            pass
        except Exception:
            pass

        if media_item is None:
            raise web.HTTPNotFound()

        # Try to get image
        image_data = None
        if media_item.metadata and media_item.metadata.images:
            for img in media_item.metadata.images:
                if img.content:
                    image_data = img.content
                    break
                if img.path:
                    try:
                        image_data = await get_image_data(self.mass, img.path)
                        if image_data:
                            break
                    except Exception:
                        continue

        if image_data is None:
            raise web.HTTPNotFound()

        return web.Response(body=image_data, content_type="image/jpeg")

    async def _handle_artist_avatar(self, request: web.Request) -> web.Response:
        """Get artist avatar image."""
        return await self._handle_get_cover_art(request)

    async def _handle_stream(self, request: web.Request) -> web.Response:
        """Stream or download a track."""
        params = dict(request.query)
        song_id = params.get("id", "")

        try:
            track = await self.mass.music.tracks.get(song_id, "library")
        except MediaNotFoundError:
            raise web.HTTPNotFound()

        if not track.provider_mappings:
            raise web.HTTPNotFound()

        # Find an available provider and get stream details
        for mapping in track.provider_mappings:
            provider = self.mass.get_provider(mapping.provider_instance)
            if provider is None:
                continue
            try:
                stream_details = await provider.get_stream_details(
                    mapping.item_id, MediaType.TRACK
                )
                if stream_details and stream_details.path:
                    # Redirect to the stream URL
                    if isinstance(stream_details.path, str):
                        raise web.HTTPFound(location=stream_details.path)
                    # Multi-part path - use the first part
                    if isinstance(stream_details.path, list) and stream_details.path:
                        raise web.HTTPFound(location=stream_details.path[0].path)
            except Exception:
                continue

        raise web.HTTPNotFound()

    async def _handle_scrobble(self, request: web.Request) -> dict[str, Any]:
        """Scrobble a track."""
        # MusicFree scrobble - just acknowledge
        return {}

    async def _handle_get_now_playing(self, request: web.Request) -> dict[str, Any]:
        """Get now playing information."""
        return {"nowPlaying": {"entry": []}}

    async def _handle_get_starred(self, request: web.Request) -> dict[str, Any]:
        """Get starred items."""
        return {"starred": {"artist": [], "album": [], "song": []}}

    async def _handle_star(self, request: web.Request) -> dict[str, Any]:
        """Star an item."""
        return {}

    async def _handle_unstar(self, request: web.Request) -> dict[str, Any]:
        """Unstar an item."""
        return {}

    async def _handle_get_user(self, request: web.Request) -> dict[str, Any]:
        """Get user information."""
        return {
            "user": {
                "username": "admin",
                "email": "",
                "scrobblingEnabled": "true",
                "adminRole": "true",
                "settingsRole": "true",
                "downloadRole": "true",
                "uploadRole": "false",
                "playlistRole": "true",
                "coverArtRole": "true",
                "commentRole": "false",
                "podcastRole": "false",
                "streamRole": "true",
                "jukeboxRole": "false",
                "shareRole": "false",
                "videoConversionRole": "false",
            }
        }

    async def _handle_get_artist_info(self, request: web.Request) -> dict[str, Any]:
        """Get artist info."""
        params = dict(request.query)
        artist_id = params.get("id", "")
        try:
            artist = await self.mass.music.artists.get(artist_id, "library")
        except MediaNotFoundError:
            artist = None

        biography = ""
        if artist and artist.metadata:
            biography = artist.metadata.biography or ""

        avatar_url = f"/rest/api/v1/artists/{artist_id}/avatar" if artist else ""
        return {
            "artistInfo": {
                "biography": biography,
                "musicBrainzId": "",
                "lastFmUrl": "",
                "smallImageUrl": avatar_url,
                "mediumImageUrl": avatar_url,
                "largeImageUrl": avatar_url,
                "similarArtist": {"artist": []},
            }
        }

    async def _handle_get_similar_songs(self, request: web.Request) -> dict[str, Any]:
        """Get similar songs."""
        return {"similarSongs": {"song": []}, "similarSongs2": {"song": []}}

    async def _handle_get_lyrics(self, request: web.Request) -> dict[str, Any]:
        """Get lyrics for a song."""
        params = dict(request.query)
        song_id = params.get("id", "")

        try:
            track = await self.mass.music.tracks.get(song_id, "library")
        except MediaNotFoundError:
            track = None

        if track and track.metadata and track.metadata.lyrics:
            lyrics = track.metadata.lyrics
            return {
                "lyricsList": {
                    "structuredLyrics": [
                        {
                            "displayArtist": track.artist.name if track.artist else "",
                            "displayTitle": track.name,
                            "lang": "und",
                            "line": [{"value": lyrics}],
                        }
                    ]
                }
            }

        return {"lyricsList": {"structuredLyrics": []}}

    async def _handle_get_extensions(self, request: web.Request) -> dict[str, Any]:
        """Get OpenSubsonic extensions."""
        return {
            "openSubsonicExtensions": [
                {"name": "transcodeOffset", "versions": [1]}
            ]
        }

    async def _handle_get_random_songs(self, request: web.Request) -> dict[str, Any]:
        """Get random songs."""
        params = dict(request.query)
        size = int(params.get("size", "10"))

        try:
            library_tracks = []
            async for track in self.mass.music.tracks.iter_library_items():
                library_tracks.append(track)
        except Exception:
            library_tracks = []

        import random as rng

        rng.shuffle(library_tracks)
        selected = library_tracks[:size]

        subsonic_songs = [
            _track_to_subsonic(t, self.instance_id, self.domain) for t in selected
        ]
        return {"randomSongs": {"song": subsonic_songs}}

    async def _handle_create_playlist(self, request: web.Request) -> dict[str, Any]:
        """Create a new playlist."""
        params = dict(request.query)
        name = params.get("name", "New Playlist")
        try:
            playlist = await self.mass.music.playlists.create_playlist(name, [MediaType.TRACK])
            return {"playlist": _playlist_to_subsonic(playlist, self.instance_id, self.domain)}
        except Exception:
            return {"error": {"code": 0, "message": "Failed to create playlist"}}

    async def _handle_update_playlist(self, request: web.Request) -> dict[str, Any]:
        """Update a playlist."""
        return {}

    async def _handle_delete_playlist(self, request: web.Request) -> dict[str, Any]:
        """Delete a playlist."""
        # Playlist deletion is handled by the MA UI, not through the bridge.
        # The bridge only provides read-only access to playlists.
        return {}

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    async def _get_all_artists(self) -> list[Artist]:
        """Get all library artists."""
        try:
            artists = []
            async for artist in self.mass.music.artists.iter_library_items():
                artists.append(artist)
            return artists
        except Exception:
            return []


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return MusicFreeProvider(mass, manifest, config, SUPPORTED_FEATURES)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    """
    Return Config entries to setup this provider.

    instance_id: id of an existing provider instance (None if new instance setup).
    action: [optional] action key called from config entries UI.
    values: the (intermediate) raw values for config entries sent with the action.
    """
    # ruff: noqa: ARG001
    return (
        ConfigEntry(
            key=CONF_PORT,
            type=ConfigEntryType.INTEGER,
            default_value=4533,
            required=True,
        ),
        ConfigEntry(
            key=CONF_TOKEN,
            type=ConfigEntryType.SECURE_STRING,
            required=False,
        ),
        ConfigEntry(
            key=CONF_SEARCH_SCOPE,
            type=ConfigEntryType.STRING,
            required=False,
        ),
    )
