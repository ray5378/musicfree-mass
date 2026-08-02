"""The provider class for the MusicFree (music-free-site) music source."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from music_assistant_models.enums import ContentType, MediaType, ProviderFeature, StreamType
from music_assistant_models.errors import (
    InvalidDataError,
    LoginFailed,
    MediaNotFoundError,
    UnsupportedFeaturedException,
)
from music_assistant_models.media_items import (
    Album,
    Artist,
    AudioFormat,
    MediaItemType,
    Playlist,
    ProviderMapping,
    RecommendationFolder,
    SearchResults,
    Track,
)
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.constants import UNKNOWN_ARTIST
from music_assistant.controllers.cache import use_cache
from music_assistant.models.music_provider import MusicProvider

from .client import (
    MusicFreeApiError,
    MusicFreeClient,
    MusicFreeConnectionError,
)
from .constants import (
    MF_CONF_BASE_URL,
    MF_CONF_PASSWORD,
    MF_CONF_PAGE_SIZE,
    MF_CONF_PATH,
    MF_CONF_PORT,
    MF_CONF_RECO_SIZE,
    MF_CONF_USERNAME,
    MF_DOMAIN,
    MF_RECO_MOST_PLAYED,
    MF_RECO_NEW_ALBUMS,
    MF_RECO_RANDOM,
    MF_RECO_STARRED,
    MF_UNKNOWN_ARTIST_ID,
    MF_VARIOUS_PREFIX,
)
from .parsers import (
    get_item_mapping,
    parse_album,
    parse_artist,
    parse_playlist,
    parse_structured_lyrics,
    parse_track,
)

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

# supported provider features (MusicFree has no podcast support)
SUPPORTED_FEATURES = {
    ProviderFeature.LIBRARY_ARTISTS,
    ProviderFeature.LIBRARY_ALBUMS,
    ProviderFeature.LIBRARY_TRACKS,
    ProviderFeature.LIBRARY_PLAYLISTS,
    ProviderFeature.BROWSE,
    ProviderFeature.SEARCH,
    ProviderFeature.RECOMMENDATIONS,
    ProviderFeature.ARTIST_ALBUMS,
    ProviderFeature.ARTIST_TOPTRACKS,
    ProviderFeature.SIMILAR_TRACKS,
    ProviderFeature.FAVORITE_ALBUMS_EDIT,
    ProviderFeature.FAVORITE_ARTISTS_EDIT,
    ProviderFeature.FAVORITE_TRACKS_EDIT,
    ProviderFeature.LYRICS,
}


class MusicFreeProvider(MusicProvider):
    """Provider for music-free-site (MusicFree) OpenSubsonic servers."""

    client: MusicFreeClient

    _reco_limit: int = 10
    _pagination_size: int = 200

    async def handle_async_init(self) -> None:
        """Set up the provider and test the connection."""
        port = self.config.get_value(MF_CONF_PORT)
        port = int(str(port)) if port is not None else None

        self.client = MusicFreeClient(
            self.mass,
            base_url=str(self.config.get_value(MF_CONF_BASE_URL) or ""),
            port=port,
            path=str(self.config.get_value(MF_CONF_PATH) or ""),
            username=str(self.config.get_value(MF_CONF_USERNAME) or ""),
            password=str(self.config.get_value(MF_CONF_PASSWORD) or ""),
        )

        try:
            await self.client.ping()
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            msg = (
                f"Failed to connect to {self.config.get_value(MF_CONF_BASE_URL)}, "
                "check your settings."
            )
            raise LoginFailed(msg) from err

        # verify credentials (probes the working token algorithm)
        await self.client.validate_auth()

        self._reco_limit = int(str(self.config.get_value(MF_CONF_RECO_SIZE, 10)))
        self._pagination_size = min(int(str(self.config.get_value(MF_CONF_PAGE_SIZE, 200))), 500)

    @property
    def is_streaming_provider(self) -> bool:
        """
        Return False since the catalog is the same as the library contents.

        This means data is unique per instance and all instances get queried.
        """
        return False

    # ------------------------------------------------------------------
    # generic helpers
    # ------------------------------------------------------------------

    def _as_media_not_found(self, err: Exception, item_id: str) -> MediaNotFoundError:
        """Convert a client error into a MediaNotFoundError."""
        return MediaNotFoundError(f"Item {item_id} not found")

    # ------------------------------------------------------------------
    # search / library
    # ------------------------------------------------------------------

    @use_cache(3600 * 3)  # cache for 3 hours
    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 20
    ) -> SearchResults:
        """Search the MusicFree library."""
        artists = limit if MediaType.ARTIST in media_types else 0
        albums = limit if MediaType.ALBUM in media_types else 0
        songs = limit if MediaType.TRACK in media_types else 0
        if not (artists or albums or songs):
            return SearchResults()
        try:
            answer = await self.client.search3(
                query=search_query,
                artist_count=artists,
                album_count=albums,
                song_count=songs,
            )
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("Search failed: %s", err)
            return SearchResults()

        result_artists = [parse_artist(self.instance_id, x) for x in answer.get("artist") or []]
        result_albums = [parse_album(self.instance_id, x) for x in answer.get("album") or []]
        result_tracks: list[Track] = []
        for entry in answer.get("song") or []:
            lyrics = await self.get_track_lyrics(entry)
            result_tracks.append(parse_track(self.instance_id, entry, lyrics=lyrics))

        return SearchResults(artists=result_artists, albums=result_albums, tracks=result_tracks)

    async def get_library_artists(self) -> AsyncGenerator[Artist]:
        """Provide a generator for reading all artists."""
        try:
            artists = await self.client.get_artists()
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("Failed to fetch artists: %s", err)
            return
        for artist in artists:
            yield parse_artist(self.instance_id, artist)

    async def get_library_albums(self) -> AsyncGenerator[Album]:
        """Provide a generator for reading all albums (paginated)."""
        offset = 0
        size = self._pagination_size
        while True:
            try:
                albums = await self.client.get_album_list2(
                    "alphabeticalByArtist", size=size, offset=offset
                )
            except (MusicFreeConnectionError, MusicFreeApiError) as err:
                self.logger.warning("Failed to fetch albums: %s", err)
                return
            if not albums:
                break
            for album in albums:
                yield parse_album(self.instance_id, album)
            if len(albums) < size:
                break
            offset += size

    async def get_library_playlists(self) -> AsyncGenerator[Playlist]:
        """Provide a generator for library playlists."""
        try:
            playlists = await self.client.get_playlists()
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("Failed to fetch playlists: %s", err)
            return
        for playlist in playlists:
            yield parse_playlist(self.instance_id, playlist)

    async def get_library_tracks(self) -> AsyncGenerator[Track]:
        """
        Provide a generator for library tracks.

        The music-free server accepts an empty search query, which returns the full
        song list, page by page.
        """
        offset = 0
        count = self._pagination_size
        while True:
            try:
                answer = await self.client.search3(
                    query="", artist_count=0, album_count=0, song_count=count, song_offset=offset
                )
            except (MusicFreeConnectionError, MusicFreeApiError) as err:
                self.logger.warning("Failed to fetch tracks: %s", err)
                return
            songs = answer.get("song") or []
            if not songs:
                break
            for entry in songs:
                lyrics = await self.get_track_lyrics(entry)
                yield parse_track(self.instance_id, entry, lyrics=lyrics)
            if len(songs) < count:
                break
            offset += count

    # ------------------------------------------------------------------
    # item lookups
    # ------------------------------------------------------------------

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_artist(self, prov_artist_id: str) -> Artist:
        """Return the requested Artist."""
        if prov_artist_id == MF_UNKNOWN_ARTIST_ID:
            return Artist(
                item_id=MF_UNKNOWN_ARTIST_ID,
                name=UNKNOWN_ARTIST,
                provider=self.instance_id,
                provider_mappings={
                    ProviderMapping(
                        item_id=MF_UNKNOWN_ARTIST_ID,
                        provider_domain=MF_DOMAIN,
                        provider_instance=self.instance_id,
                    )
                },
            )
        if prov_artist_id.startswith(MF_VARIOUS_PREFIX):
            # fake artist id built for various-artists tracks
            return Artist(
                item_id=prov_artist_id,
                name=prov_artist_id.removeprefix(MF_VARIOUS_PREFIX),
                provider=self.instance_id,
                provider_mappings={
                    ProviderMapping(
                        item_id=prov_artist_id,
                        provider_domain=MF_DOMAIN,
                        provider_instance=self.instance_id,
                    )
                },
            )
        try:
            sonic_artist = await self.client.get_artist(prov_artist_id)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise self._as_media_not_found(err, prov_artist_id) from err
        if not sonic_artist:
            raise MediaNotFoundError(f"Artist {prov_artist_id} not found")
        return parse_artist(self.instance_id, sonic_artist)

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_artist_albums(self, prov_artist_id: str) -> list[Album]:
        """Return a list of all Albums by the specified Artist."""
        if prov_artist_id == MF_UNKNOWN_ARTIST_ID or prov_artist_id.startswith(MF_VARIOUS_PREFIX):
            return []
        try:
            sonic_artist = await self.client.get_artist(prov_artist_id)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise self._as_media_not_found(err, prov_artist_id) from err
        if not sonic_artist:
            raise MediaNotFoundError(f"Artist {prov_artist_id} not found")
        return [parse_album(self.instance_id, x) for x in sonic_artist.get("album") or []]

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_artist_toptracks(self, prov_artist_id: str) -> list[Track]:
        """
        Get the top listed tracks for the specified artist.

        NOTE: the music-free server implements a non-standard getArtistInfo2 which
        returns artist + similarArtists + topSongs at the root of the response.
        """
        if prov_artist_id == MF_UNKNOWN_ARTIST_ID or prov_artist_id.startswith(MF_VARIOUS_PREFIX):
            return []
        try:
            artist_info = await self.client.get_artist_info2(
                prov_artist_id, count=self._reco_limit
            )
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("getArtistInfo2 failed: %s", err)
            return []
        songs = (artist_info.get("topSongs") or {}).get("song") or []
        tracks: list[Track] = []
        for entry in songs:
            lyrics = await self.get_track_lyrics(entry)
            tracks.append(parse_track(self.instance_id, entry, lyrics=lyrics))
        return tracks

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_album(self, prov_album_id: str) -> Album:
        """Return the requested Album."""
        try:
            sonic_album = await self.client.get_album(prov_album_id)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise self._as_media_not_found(err, prov_album_id) from err
        if not sonic_album:
            raise MediaNotFoundError(f"Album {prov_album_id} not found")
        return parse_album(self.instance_id, sonic_album)

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_album_tracks(self, prov_album_id: str) -> list[Track]:
        """Return a list of tracks on the specified Album."""
        try:
            sonic_album = await self.client.get_album(prov_album_id)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise self._as_media_not_found(err, prov_album_id) from err
        if not sonic_album:
            raise MediaNotFoundError(f"Album {prov_album_id} not found")
        tracks: list[Track] = []
        for entry in sonic_album.get("song") or []:
            lyrics = await self.get_track_lyrics(entry)
            tracks.append(parse_track(self.instance_id, entry, lyrics=lyrics))
        return tracks

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_track(self, prov_track_id: str) -> Track:
        """Return the specified track."""
        try:
            sonic_song = await self.client.get_song(prov_track_id)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise self._as_media_not_found(err, prov_track_id) from err
        if not sonic_song:
            raise MediaNotFoundError(f"Track {prov_track_id} not found")
        album: Album | None = None
        album_id = sonic_song.get("albumId") or sonic_song.get("parent")
        if album_id:
            try:
                album = await self.get_album(prov_album_id=str(album_id))
            except MediaNotFoundError:
                album = None
        lyrics = await self.get_track_lyrics(sonic_song)
        return parse_track(self.instance_id, sonic_song, album=album, lyrics=lyrics)

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_playlist(self, prov_playlist_id: str) -> Playlist:
        """Return the specified Playlist."""
        try:
            sonic_playlist = await self.client.get_playlist(prov_playlist_id)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise self._as_media_not_found(err, prov_playlist_id) from err
        if not sonic_playlist:
            raise MediaNotFoundError(f"Playlist {prov_playlist_id} not found")
        return parse_playlist(self.instance_id, sonic_playlist)

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_playlist_tracks(self, prov_playlist_id: str, page: int = 0) -> list[Track]:
        """Get playlist tracks."""
        result: list[Track] = []
        if page > 0:
            # paging not supported, we always return the whole list at once
            return result
        try:
            sonic_playlist = await self.client.get_playlist(prov_playlist_id)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise self._as_media_not_found(err, prov_playlist_id) from err
        if not sonic_playlist:
            raise MediaNotFoundError(f"Playlist {prov_playlist_id} not found")

        for index, entry in enumerate(sonic_playlist.get("entry") or [], 1):
            lyrics = await self.get_track_lyrics(entry)
            track = parse_track(self.instance_id, entry, lyrics=lyrics)
            track.position = index
            result.append(track)
        return result

    @use_cache(3600 * 3)  # cache for 3 hours
    async def get_similar_tracks(self, prov_track_id: str, limit: int = 25) -> list[Track]:
        """Get tracks similar to the selected track."""
        try:
            songs = await self.client.get_similar_songs(prov_track_id, count=limit)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.info("getSimilarSongs failed: %s", err)
            return []
        tracks: list[Track] = []
        for entry in songs:
            lyrics = await self.get_track_lyrics(entry)
            tracks.append(parse_track(self.instance_id, entry, lyrics=lyrics))
        return tracks

    # ------------------------------------------------------------------
    # favorites
    # ------------------------------------------------------------------

    async def set_favorite(self, prov_item_id: str, media_type: MediaType, favorite: bool) -> None:
        """Set or clear favorite on the server."""
        if media_type not in (MediaType.ARTIST, MediaType.ALBUM, MediaType.TRACK):
            return
        await self.client.set_starred(
            track_ids=[prov_item_id] if media_type == MediaType.TRACK else None,
            album_ids=[prov_item_id] if media_type == MediaType.ALBUM else None,
            artist_ids=[prov_item_id] if media_type == MediaType.ARTIST else None,
            starred=favorite,
        )

    # ------------------------------------------------------------------
    # recommendations
    # ------------------------------------------------------------------

    @use_cache(3600 * 3, cache_checksum="v2")  # cache for 3 hours
    async def recommendations(self) -> list[RecommendationFolder]:
        """Provide the recommendations (starred, new, most played and random)."""
        recos: list[RecommendationFolder] = []

        starred: RecommendationFolder = RecommendationFolder(
            item_id=MF_RECO_STARRED,
            provider=self.instance_id,
            name="Starred Items",
            translation_key="media.recommendations.starred_items",
        )
        try:
            starred_items = await self.client.get_starred()
            for album in (starred_items.get("album") or [])[: self._reco_limit]:
                starred.items.append(parse_album(self.instance_id, album))
            for artist in (starred_items.get("artist") or [])[: self._reco_limit]:
                starred.items.append(parse_artist(self.instance_id, artist))
            for song in (starred_items.get("song") or [])[: self._reco_limit]:
                lyrics = await self.get_track_lyrics(song)
                starred.items.append(parse_track(self.instance_id, song, lyrics=lyrics))
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("Failed to fetch starred items: %s", err)
        recos.append(starred)

        new_albums: RecommendationFolder = RecommendationFolder(
            item_id=MF_RECO_NEW_ALBUMS,
            provider=self.instance_id,
            name="New Albums",
            translation_key="media.recommendations.recently_added_albums",
        )
        try:
            for album in await self.client.get_album_list2("newest", size=self._reco_limit):
                new_albums.items.append(parse_album(self.instance_id, album))
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("Failed to fetch new albums: %s", err)
        recos.append(new_albums)

        most_played: RecommendationFolder = RecommendationFolder(
            item_id=MF_RECO_MOST_PLAYED,
            provider=self.instance_id,
            name="Most Played Albums",
            translation_key="media.recommendations.most_played_albums",
        )
        try:
            for album in await self.client.get_album_list2("frequent", size=self._reco_limit):
                most_played.items.append(parse_album(self.instance_id, album))
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("Failed to fetch most played albums: %s", err)
        recos.append(most_played)

        random_songs: RecommendationFolder = RecommendationFolder(
            item_id=MF_RECO_RANDOM,
            provider=self.instance_id,
            name="Random Songs",
            translation_key="media.recommendations.random_songs",
        )
        try:
            for song in await self.client.get_random_songs(size=self._reco_limit):
                lyrics = await self.get_track_lyrics(song)
                random_songs.items.append(parse_track(self.instance_id, song, lyrics=lyrics))
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("Failed to fetch random songs: %s", err)
        recos.append(random_songs)

        return recos

    # ------------------------------------------------------------------
    # lyrics
    # ------------------------------------------------------------------

    async def get_track_lyrics(self, track: dict[str, Any]) -> tuple[str, bool] | None:
        """
        Get lyrics for a track.

        Prefers the newer getLyricsBySongId endpoint, falls back to the legacy
        getLyrics (artist + title) endpoint.
        """
        try:
            lyrics_list = await self.client.get_lyrics_by_song_id(str(track["id"]))
        except (MusicFreeConnectionError, MusicFreeApiError):
            lyrics_list = []
        if lyrics_list:
            try:
                return parse_structured_lyrics(lyrics_list[0])
            except (InvalidDataError, MusicFreeConnectionError):
                pass

        try:
            ly = await self.client.get_lyrics(
                str(track.get("artist", "")), str(track.get("title", ""))
            )
        except (MusicFreeConnectionError, MusicFreeApiError):
            return None
        if not ly or not ly.get("value"):
            return None
        value = str(ly["value"])
        return (value, value.startswith("["))

    # ------------------------------------------------------------------
    # playback / streaming
    # ------------------------------------------------------------------

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Get the details needed to stream a specified track."""
        if media_type != MediaType.TRACK:
            msg = f"Unsupported media type encountered '{media_type}'"
            raise UnsupportedFeaturedException(msg)

        try:
            item = await self.client.get_song(item_id)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise MediaNotFoundError(f"Item {item_id} not found") from err
        if not item:
            raise MediaNotFoundError(f"Item {item_id} not found")

        self.logger.debug(
            "Fetching stream details for id %s '%s'",
            item_id,
            item.get("title"),
        )

        return StreamDetails(
            item_id=item_id,
            provider=self.instance_id,
            allow_seek=True,
            can_seek=True,
            media_type=media_type,
            # let ffmpeg figure out the actual container: the server transcodes
            # (e.g. to MP3) when seeking via timeOffset so the content type of the
            # original file is not a reliable indicator of what comes down the wire
            audio_format=AudioFormat(
                content_type=ContentType.try_parse("?"),
                sample_rate=44100,
                bit_depth=16,
                channels=2,
                bit_rate=int(str(item.get("bitRate") or 0)) or None,
            ),
            stream_type=StreamType.CUSTOM,
            duration=int(str(item.get("duration") or 0)),
            size=int(str(item.get("size") or 0)) or None,
        )

    async def get_audio_stream(
        self, streamdetails: StreamDetails, seek_position: int = 0
    ) -> AsyncGenerator[bytes]:
        """Provide a generator for the stream data."""
        self.logger.debug("Streaming %s", streamdetails.item_id)
        try:
            resp = await self.client.stream(streamdetails.item_id, time_offset=seek_position)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise MediaNotFoundError(f"Item '{streamdetails.item_id}' not found") from err
        self.logger.debug("starting stream of item '%s'", streamdetails.item_id)
        try:
            async with resp:
                async for chunk in resp.content.iter_chunked(40960):
                    yield bytes(chunk)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            raise MediaNotFoundError(f"Item '{streamdetails.item_id}' not found") from err
        self.logger.debug("Done streaming %s", streamdetails.item_id)

    # ------------------------------------------------------------------
    # image resolution
    # ------------------------------------------------------------------

    async def resolve_image(self, path: str) -> str | bytes | None:
        """Return the cover art image bytes for the given (prefixed) art id."""
        self.logger.debug("Requesting cover art for '%s'", path)
        try:
            return await self.client.get_cover_art(path)
        except (MusicFreeConnectionError, MusicFreeApiError) as err:
            self.logger.warning("Unable to locate a cover image for %s: %s", path, err)
            return None

    # ------------------------------------------------------------------
    # playback reporting (scrobble)
    # ------------------------------------------------------------------

    async def on_played(
        self,
        media_type: MediaType,
        prov_item_id: str,
        fully_played: bool,
        position: int,
        media_item: MediaItemType,
        is_playing: bool = False,
    ) -> None:
        """Report playback progress to the server (best-effort scrobble)."""
        if media_type != MediaType.TRACK:
            return
        if is_playing:
            await self.client.scrobble(prov_item_id, submission=False)
        elif fully_played or position > 0:
            await self.client.scrobble(prov_item_id, time_ms=position * 1000, submission=True)
