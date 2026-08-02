"""Async OpenSubsonic API client tailored to the music-free-site server.

The music-free-site server implements the OpenSubsonic/Subsonic API with a few
non-standard quirks (see https://ansgoo.github.io/music-free-site/opensubsonic-api):

* ``getArtistInfo2`` returns ``artist`` + ``similarArtists`` + ``topSongs`` all at
  the root of the response (instead of a nested ``artistInfo2`` element).
* ``getTopSongs`` expects the **artist name** (exact library match), not an id.
* ``search2``/``search3`` accept an empty ``query`` (offline sync support).
* Both legacy (``md5(password + salt)``) and OpenSubsonic
  (``md5(md5(password) + salt)``) token authentication are accepted.

This client is fully self-contained (no third party Subsonic library) so we can
handle those quirks explicitly.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from music_assistant_models.errors import LoginFailed, MediaNotFoundError

from .constants import MF_CLIENT_NAME, MF_DOMAIN, MF_PROTOCOL_VERSION

if TYPE_CHECKING:
    from aiohttp import ClientSession

    from music_assistant.mass import MusicAssistant

LOGGER = logging.getLogger(__name__)


class MusicFreeAuthError(Exception):
    """Raised when the server rejects our credentials."""


class MusicFreeConnectionError(Exception):
    """Raised when the server can not be reached."""


class MusicFreeApiError(Exception):
    """Raised when the server returns an (unsuccessful) error response."""


# ----------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------


def _make_token(password: str, salt: str, *, double_hash: bool) -> str:
    """Build the OpenSubsonic ``t`` token for the given salt."""
    if double_hash:
        inner = hashlib.md5(password.encode("utf-8")).hexdigest()
        raw = f"{inner}{salt}"
    else:
        raw = f"{password}{salt}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _as_int(value: Any) -> int:
    """Convert an (possibly string) value to int, returning 0 when not possible."""
    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_str(value: Any, default: str = "") -> str:
    """Return a string value, guarding against None."""
    return str(value) if value is not None else default


# ----------------------------------------------------------------------------------
# client
# ----------------------------------------------------------------------------------


class MusicFreeClient:
    """Async client for the music-free-site OpenSubsonic API."""

    def __init__(
        self,
        mass: MusicAssistant,
        *,
        base_url: str,
        port: int | None = None,
        path: str = "",
        username: str,
        password: str,
    ) -> None:
        """Initialize the client."""
        self.mass = mass
        self._base_url = base_url or ""
        self._port = int(port) if port else None
        self._path = path or ""
        self._username = username or ""
        self._password = password or ""
        self._double_hash_auth: bool | None = None

    # ------------------------------------------------------------------
    # url / auth helpers
    # ------------------------------------------------------------------

    def _server_base(self) -> str:
        """Return the (scheme://host[:port][/path]) server address."""
        base = self._base_url.strip()
        if base.startswith("http://") or base.startswith("https://"):
            scheme, _, rest = base.partition("://")
            host = rest.rstrip("/")
        else:
            scheme = "http"
            host = base.strip("/")
        url = f"{scheme}://{host}"
        if self._port:
            url = f"{url}:{self._port}"
        if self._path:
            path = self._path.strip("/")
            if path:
                url = f"{url}/{path}"
        return url

    def _rest_base(self) -> str:
        """Return the base url where the OpenSubsonic endpoints live."""
        return f"{self._server_base()}/rest"

    def _auth_params(self) -> dict[str, str]:
        """Return the auth query params (u/t/s) for a request."""
        if self._double_hash_auth is None:
            # unknown variant yet, fall back to the OpenSubsonic default
            self._double_hash_auth = True
        salt = secrets.token_hex(6)
        token = _make_token(self._password, salt, double_hash=self._double_hash_auth)
        return {"u": self._username, "t": token, "s": salt}

    def _base_params(self) -> dict[str, str]:
        """Return the common params appended to every request."""
        params: dict[str, str] = {"v": MF_PROTOCOL_VERSION, "c": MF_CLIENT_NAME, "f": "json"}
        params.update(self._auth_params())
        return params

    def build_url(self, endpoint: str, params: Mapping[str, Any] | None = None) -> str:
        """Build a full request url (with auth) for the given endpoint."""
        query = self._base_params()
        if params:
            for key, value in params.items():
                if value is None:
                    continue
                if isinstance(value, bool):
                    query[key] = "true" if value else "false"
                else:
                    query[key] = str(value)
        encoded = urlencode(query)
        return f"{self._rest_base()}/{endpoint}?{encoded}"

    # ------------------------------------------------------------------
    # low level request handling
    # ------------------------------------------------------------------

    @property
    def _session(self) -> ClientSession:
        return self.mass.http_session

    async def _request(
        self, endpoint: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform a GET request and return the (parsed) 'subsonic-response' payload."""
        url = self.build_url(endpoint, params)
        try:
            async with self._session.get(url) as response:
                payload = await response.json(content_type=None)
                if response.status not in (200, 201):
                    # servers may signal errors with a JSON body on non-2xx status
                    raise MusicFreeApiError(
                        f"Server returned HTTP status {response.status} for {endpoint}"
                    )
        except asyncio.TimeoutError as err:
            raise MusicFreeConnectionError(f"Timeout fetching {endpoint}") from err
        except (MusicFreeConnectionError, MusicFreeApiError):
            raise
        except Exception as err:
            raise MusicFreeConnectionError(f"Error fetching {endpoint}: {err}") from err

        return self._parse_payload(payload)

    async def _request_retry_auth(
        self, endpoint: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform a request, probing both token algorithms on the first call."""
        if self._double_hash_auth is not None:
            # we already know which algorithm the server accepts
            return await self._request(endpoint, params)
        last_error: Exception | None = None
        for double_hash in (True, False):
            self._double_hash_auth = double_hash
            try:
                return await self._request(endpoint, params)
            except MusicFreeAuthError as err:
                self.logger.debug(
                    "Auth variant md5(%spw+salt) rejected, trying alternate",
                    "md5(" if double_hash else "",
                )
                last_error = err
                continue
            except MusicFreeConnectionError:
                self._double_hash_auth = None
                raise
        self._double_hash_auth = None
        raise last_error or MusicFreeAuthError("All authentication attempts failed")

    def _parse_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate the 'subsonic-response' envelope and return its contents."""
        response = payload.get("subsonic-response") or payload
        status = response.get("status")
        if status == "failed":
            error = response.get("error") or {}
            error_code = error.get("code")
            message = error.get("message") or "Unknown error"
            if error_code in (40, 41):
                raise MusicFreeAuthError(f"Authentication failed: {message}")
            raise MusicFreeApiError(f"Server error ({error_code}): {message}")
        if status is None:
            raise MusicFreeConnectionError("Response has no 'status' field")
        return response

    async def _request_binary(
        self, endpoint: str, params: Mapping[str, Any] | None = None
    ) -> bytes:
        """Perform a GET request that returns raw (binary) data."""
        url = self.build_url(endpoint, params)
        try:
            async with self._session.get(url) as response:
                if response.status != 200:
                    # errors are JSON, try to surface a useful message
                    try:
                        payload = await response.json(content_type=None)
                        self._parse_payload(payload)
                    except Exception:
                        pass
                    raise MusicFreeConnectionError(
                        f"Unexpected HTTP status {response.status} for {endpoint}"
                    )
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
                if content_type and not content_type.startswith("image/"):
                    # binary endpoints such as getCoverArt signal "not found" as
                    # an HTTP 200 with an XML/JSON error body; surface the message
                    try:
                        payload = await response.json(content_type=None)
                        self._parse_payload(payload)
                    except (MusicFreeApiError, MusicFreeAuthError):
                        raise
                    except Exception:
                        pass
                    raise MusicFreeConnectionError(
                        f"Unexpected content type {content_type} for {endpoint}"
                    )
                return await response.read()
        except MusicFreeConnectionError:
            raise
        except Exception as err:
            raise MusicFreeConnectionError(f"Error fetching {endpoint}: {err}") from err

    # ------------------------------------------------------------------
    # logging
    # ------------------------------------------------------------------

    @property
    def logger(self) -> logging.Logger:
        """Return the logger for this client."""
        return LOGGER

    # ------------------------------------------------------------------
    # connection handling
    # ------------------------------------------------------------------

    async def ping(self) -> dict[str, Any]:
        """Call the ping endpoint (no auth required)."""
        url = f"{self._rest_base()}/ping"
        params: dict[str, str] = {
            "v": MF_PROTOCOL_VERSION,
            "c": MF_CLIENT_NAME,
            "f": "json",
        }
        url = f"{url}?{urlencode(params)}"
        try:
            async with self._session.get(url) as response:
                if response.status not in (200, 201):
                    raise MusicFreeConnectionError(
                        f"Unexpected HTTP status {response.status} for ping"
                    )
                payload = await response.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise MusicFreeConnectionError("Timeout while pinging server") from err
        except Exception as err:
            raise MusicFreeConnectionError(f"Error while pinging server: {err}") from err
        return self._parse_payload(payload)

    async def validate_auth(self) -> None:
        """Verify our credentials against the server (used during setup/init)."""
        # try the OpenSubsonic (double hash) token first, fall back to the legacy
        # single hash variant, then fail with a LoginFailed-style error.
        try:
            await self._request_retry_auth("getLicense")
        except MusicFreeAuthError as err:
            raise LoginFailed(
                "Invalid username or password for the MusicFree server. "
                "Check your settings and try again.",
                translation_key="auth_failed",
                translation_owner=f"provider.{MF_DOMAIN}",
            ) from err
        except MusicFreeApiError as err:
            # server reachable but unhappy with something else; surface it
            LOGGER.warning("Server rejected validate_auth request: %s", err)

    # ------------------------------------------------------------------
    # library / metadata endpoints
    # ------------------------------------------------------------------

    async def get_artists(self) -> list[dict[str, Any]]:
        """Return all artists (flattened from the getArtists index response)."""
        result = await self._request("getArtists")
        artists_index = result.get("artists") or result.get("indexes") or {}
        items: list[dict[str, Any]] = []
        for index in artists_index.get("index") or []:
            items.extend(index.get("artist") or [])
        return items

    async def get_artist(self, artist_id: str) -> dict[str, Any] | None:
        """Return full details (incl. album list) for the given artist."""
        result = await self._request("getArtist", {"id": artist_id})
        return result.get("artist")

    async def get_artist_info2(
        self, artist_id: str, count: int = 50
    ) -> dict[str, Any]:
        """Return getArtistInfo2 (artist + similarArtists + topSongs)."""
        return await self._request("getArtistInfo2", {"id": artist_id, "count": count})

    async def get_album(self, album_id: str) -> dict[str, Any] | None:
        """Return the album (incl. its tracks)."""
        result = await self._request("getAlbum", {"id": album_id})
        return result.get("album")

    async def get_album_list2(
        self,
        list_type: str,
        size: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a page of albums for the given list type."""
        result = await self._request(
            "getAlbumList2", {"type": list_type, "size": size, "offset": offset}
        )
        album_list = result.get("albumList2") or result.get("albumList") or {}
        return album_list.get("album") or []

    async def get_song(self, song_id: str) -> dict[str, Any] | None:
        """Return full details for the given song."""
        result = await self._request("getSong", {"id": song_id})
        return result.get("song")

    async def get_random_songs(self, size: int = 20) -> list[dict[str, Any]]:
        """Return a list of random songs."""
        result = await self._request("getRandomSongs", {"size": size})
        return (result.get("randomSongs") or {}).get("song") or []

    async def get_playlists(self) -> list[dict[str, Any]]:
        """Return all playlists visible to the current user."""
        result = await self._request("getPlaylists")
        return (result.get("playlists") or {}).get("playlist") or []

    async def get_playlist(self, playlist_id: str) -> dict[str, Any] | None:
        """Return a single playlist (incl. its entries)."""
        result = await self._request("getPlaylist", {"id": playlist_id})
        return result.get("playlist")

    async def search3(
        self,
        query: str,
        *,
        artist_count: int = 20,
        artist_offset: int = 0,
        album_count: int = 20,
        album_offset: int = 0,
        song_count: int = 20,
        song_offset: int = 0,
    ) -> dict[str, Any]:
        """Perform a search and return the searchResult3 dict."""
        result = await self._request(
            "search3",
            {
                "query": query,
                "artistCount": artist_count,
                "artistOffset": artist_offset,
                "albumCount": album_count,
                "albumOffset": album_offset,
                "songCount": song_count,
                "songOffset": song_offset,
            },
        )
        return result.get("searchResult3") or result.get("searchResult2") or {}

    async def get_similar_songs(self, song_id: str, count: int = 50) -> list[dict[str, Any]]:
        """Return songs similar to the given song."""
        result = await self._request("getSimilarSongs", {"id": song_id, "count": count})
        return (result.get("similarSongs") or {}).get("song") or []

    async def get_top_songs(self, artist_name: str, count: int = 50) -> list[dict[str, Any]]:
        """Return the most played songs for the given artist (by name)."""
        result = await self._request("getTopSongs", {"artist": artist_name, "count": count})
        return (result.get("topSongs") or {}).get("song") or []

    async def get_starred(self) -> dict[str, Any]:
        """Return the starred items (getStarred2)."""
        return await self._request("getStarred2")

    async def set_starred(
        self,
        *,
        track_ids: list[str] | None = None,
        album_ids: list[str] | None = None,
        artist_ids: list[str] | None = None,
        starred: bool,
    ) -> None:
        """Star/unstar items on the server."""
        endpoint = "star" if starred else "unstar"
        params: dict[str, Any] = {}
        if track_ids:
            params["id"] = track_ids
        if album_ids:
            params["albumId"] = album_ids
        if artist_ids:
            params["artistId"] = artist_ids
        await self._request(endpoint, params)

    # ------------------------------------------------------------------
    # lyrics
    # ------------------------------------------------------------------

    async def get_lyrics_by_song_id(self, song_id: str) -> list[dict[str, Any]]:
        """Return the structured lyrics list for the given song id."""
        result = await self._request("getLyricsBySongId", {"id": song_id})
        return (result.get("lyricsList") or {}).get("structuredLyrics") or []

    async def get_lyrics(self, artist: str, title: str) -> dict[str, Any] | None:
        """Return (plain/LRC) lyrics via the legacy getLyrics endpoint."""
        result = await self._request("getLyrics", {"artist": artist, "title": title})
        return result.get("lyrics")

    # ------------------------------------------------------------------
    # binary endpoints
    # ------------------------------------------------------------------

    async def get_cover_art(self, art_id: str, size: int | None = None) -> bytes:
        """Return the raw cover art image bytes for the given (prefixed) art id."""
        params: dict[str, Any] = {"id": art_id}
        if size:
            params["size"] = size
        return await self._request_binary("getCoverArt", params)

    def get_stream_url(self, song_id: str) -> str:
        """Return a (GET) stream url for the given song id, with auth embedded."""
        return self.build_url("stream", {"id": song_id})

    async def stream(self, song_id: str, time_offset: int = 0) -> Any:
        """
        Open a streaming connection to the given song.

        Returns the raw (aiohttp) response object; the caller consumes
        ``response.content``. ``time_offset`` is passed as ``timeOffset`` (the
        music-free server does not honour HTTP Range requests, so seeking has to
        happen server side through this parameter).

        The server signals errors (e.g. unknown id) as an HTTP 200 with a JSON
        body instead of a non-2xx status, so we sniff the response and raise a
        proper error when that is the case.
        """
        params: dict[str, Any] = {"id": song_id}
        if time_offset:
            params["timeOffset"] = time_offset
        url = self.build_url("stream", params)
        try:
            response = await self._session.get(url)
        except asyncio.TimeoutError as err:
            raise MusicFreeConnectionError(f"Timeout streaming {song_id}") from err
        except Exception as err:
            raise MusicFreeConnectionError(f"Error streaming {song_id}: {err}") from err

        if response.status != 200:
            # some servers return JSON errors with a non-2xx status
            try:
                payload = await response.json(content_type=None)
                self._parse_payload(payload)
            except Exception:
                pass
            await response.release()
            raise MusicFreeConnectionError(
                f"Unexpected HTTP status {response.status} while streaming {song_id}"
            )

        content_type = response.headers.get("content-type", "").lower()
        if "json" in content_type:
            # music-free reports failures as HTTP 200 + JSON error body
            payload = await response.json(content_type=None)
            await response.release()
            return self._parse_payload(payload)
        return response

    async def scrobble(
        self,
        song_id: str,
        *,
        time_ms: int = 0,
        submission: bool = False,
    ) -> None:
        """Report playback to the server (scrobble / now playing)."""
        params: dict[str, Any] = {"id": song_id}
        if time_ms:
            params["time"] = time_ms
        params["submission"] = submission
        try:
            await self._request("scrobble", params)
        except (MusicFreeApiError, MusicFreeConnectionError) as err:
            # scrobbling is best-effort, never fail playback on it
            LOGGER.debug("Failed to scrobble %s: %s", song_id, err)
