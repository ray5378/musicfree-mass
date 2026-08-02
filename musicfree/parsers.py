"""Parse music-free-site OpenSubsonic JSON payloads into Music Assistant models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from music_assistant_models.enums import ContentType, ImageType, MediaType
from music_assistant_models.errors import InvalidDataError, MediaNotFoundError
from music_assistant_models.media_items import (
    Album,
    Artist,
    AudioFormat,
    ItemMapping,
    MediaItemImage,
    MediaItemMetadata,
    Playlist,
    ProviderMapping,
    Track,
)

from music_assistant.constants import UNKNOWN_ARTIST
from music_assistant.helpers.util import parse_title_and_version

from .constants import (
    MF_DOMAIN,
    MF_UNKNOWN_ARTIST_ID,
    MF_VARIOUS_PREFIX,
)

# ----------------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------------


def _first(*values: Any, default: str = "") -> str:
    """Return the first non-empty string value."""
    for value in values:
        if value is not None and str(value) != "":
            return str(value)
    return default


def _as_int(value: Any) -> int:
    """Convert a value to int, returning 0 when that is not possible."""
    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_bool(value: Any) -> bool:
    """Convert a value to bool, guarding against string booleans."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")


def _add_image(metadata: MediaItemMetadata, instance_id: str, path: str | None) -> None:
    """Add a thumb image to the metadata if a path is present."""
    if not path:
        return
    metadata.add_image(
        MediaItemImage(
            type=ImageType.THUMB,
            path=path,
            provider=instance_id,
            remotely_accessible=False,
        )
    )


def _get_audio_format(song: dict[str, Any]) -> AudioFormat:
    """Build an AudioFormat from a song payload."""
    return AudioFormat(
        content_type=ContentType.try_parse(song.get("contentType") or "?"),
        sample_rate=44100,
        bit_depth=16,
        channels=2,
        bit_rate=_as_int(song.get("bitRate")) or None,
    )


# ----------------------------------------------------------------------------------
# track
# ----------------------------------------------------------------------------------


def parse_track(
    instance_id: str,
    song: dict[str, Any],
    album: Album | ItemMapping | None = None,
    lyrics: tuple[str, bool] | None = None,
) -> Track:
    """Parse an OpenSubsonic 'child'/'song' payload into an MA Track."""
    if not album:
        album_id = song.get("albumId") or song.get("parent")
        album_name = _first(song.get("album"), song.get("parent_name"))
        if album_id and album_name:
            album = get_item_mapping(instance_id, MediaType.ALBUM, str(album_id), album_name)

    metadata: MediaItemMetadata = MediaItemMetadata()

    if lyrics:
        ly, synced = lyrics
        if synced:
            metadata.lrc_lyrics = ly
        else:
            metadata.lyrics = ly

    if genre := song.get("genre"):
        if not metadata.genres:
            metadata.genres = set()
        metadata.genres.add(str(genre))

    name, version = parse_title_and_version(_first(song.get("title"), song.get("name"), "?"))

    track = Track(
        item_id=str(song.get("id") or song.get("item_id")),
        provider=instance_id,
        name=name,
        version=version,
        album=album,
        duration=_as_int(song.get("duration")),
        disc_number=_as_int(song.get("discNumber") or song.get("disc")),
        favorite=_as_bool(song.get("starred")),
        metadata=metadata,
        provider_mappings={
            ProviderMapping(
                item_id=str(song.get("id") or song.get("item_id")),
                provider_domain=MF_DOMAIN,
                provider_instance=instance_id,
                available=True,
                audio_format=_get_audio_format(song),
            )
        },
        track_number=_as_int(song.get("track")),
    )

    if year := _as_int(song.get("year")):
        track.metadata.release_date = datetime(year, 1, 1)

    _add_image(metadata, instance_id, song.get("coverArt"))

    # artists: prefer the explicit id, then the artists array, then name-only fallbacks
    if artist_id := song.get("artistId"):
        track.artists.append(
            get_item_mapping(
                instance_id,
                MediaType.ARTIST,
                str(artist_id),
                _first(song.get("artist"), UNKNOWN_ARTIST),
            )
        )

    for entry in song.get("artists") or []:
        if isinstance(entry, dict) and entry.get("id") and entry.get("name"):
            if entry.get("id") == song.get("artistId"):
                continue
            track.artists.append(
                get_item_mapping(instance_id, MediaType.ARTIST, str(entry["id"]), str(entry["name"]))
            )

    if not track.artists:
        if artist_name := song.get("artist"):
            # name-only artist (e.g. various-artists albums): build a fake id
            fake_id = f"{MF_VARIOUS_PREFIX}{artist_name}"
            artist = Artist(
                item_id=fake_id,
                provider=instance_id,
                name=str(artist_name),
                provider_mappings={
                    ProviderMapping(
                        item_id=fake_id,
                        provider_domain=MF_DOMAIN,
                        provider_instance=instance_id,
                    )
                },
            )
        else:
            artist = Artist(
                item_id=MF_UNKNOWN_ARTIST_ID,
                name=UNKNOWN_ARTIST,
                provider=instance_id,
                provider_mappings={
                    ProviderMapping(
                        item_id=MF_UNKNOWN_ARTIST_ID,
                        provider_domain=MF_DOMAIN,
                        provider_instance=instance_id,
                    )
                },
            )
        track.artists.append(artist)

    return track


# ----------------------------------------------------------------------------------
# artist
# ----------------------------------------------------------------------------------


def parse_artist(instance_id: str, artist: dict[str, Any]) -> Artist:
    """Parse an OpenSubsonic artist payload into an MA Artist."""
    metadata: MediaItemMetadata = MediaItemMetadata()
    _add_image(metadata, instance_id, artist.get("coverArt"))

    if bio := artist.get("biography"):
        metadata.description = str(bio)

    parsed = Artist(
        item_id=str(artist.get("id")),
        name=_first(artist.get("name"), artist.get("artist"), "?"),
        metadata=metadata,
        provider=instance_id,
        favorite=_as_bool(artist.get("starred")),
        provider_mappings={
            ProviderMapping(
                item_id=str(artist.get("id")),
                provider_domain=MF_DOMAIN,
                provider_instance=instance_id,
            )
        },
    )
    return parsed


# ----------------------------------------------------------------------------------
# album
# ----------------------------------------------------------------------------------


def parse_album(instance_id: str, album: dict[str, Any]) -> Album:
    """Parse an OpenSubsonic album payload into an MA Album."""
    metadata: MediaItemMetadata = MediaItemMetadata()
    _add_image(metadata, instance_id, album.get("coverArt"))

    if genre := album.get("genre"):
        if not metadata.genres:
            metadata.genres = set()
        metadata.genres.add(str(genre))

    name, version = parse_title_and_version(
        _first(album.get("name"), album.get("album"), album.get("title"), "?")
    )

    parsed = Album(
        item_id=str(album.get("id")),
        provider=instance_id,
        metadata=metadata,
        name=name,
        version=version,
        favorite=_as_bool(album.get("starred")),
        provider_mappings={
            ProviderMapping(
                item_id=str(album.get("id")),
                provider_domain=MF_DOMAIN,
                provider_instance=instance_id,
            )
        },
        year=_as_int(album.get("year")) or None,
    )

    if artist_id := album.get("artistId"):
        parsed.artists.append(
            get_item_mapping(
                instance_id,
                MediaType.ARTIST,
                str(artist_id),
                _first(album.get("artist"), UNKNOWN_ARTIST),
            )
        )
    elif not album.get("artists"):
        parsed.artists.append(
            Artist(
                item_id=MF_UNKNOWN_ARTIST_ID,
                name=UNKNOWN_ARTIST,
                provider=instance_id,
                provider_mappings={
                    ProviderMapping(
                        item_id=MF_UNKNOWN_ARTIST_ID,
                        provider_domain=MF_DOMAIN,
                        provider_instance=instance_id,
                    )
                },
            )
        )

    for entry in album.get("artists") or []:
        if isinstance(entry, dict) and entry.get("id") and entry.get("name"):
            if entry.get("id") == album.get("artistId"):
                continue
            parsed.artists.append(
                get_item_mapping(instance_id, MediaType.ARTIST, str(entry["id"]), str(entry["name"]))
            )

    return parsed


# ----------------------------------------------------------------------------------
# playlist
# ----------------------------------------------------------------------------------


def parse_playlist(instance_id: str, playlist: dict[str, Any]) -> Playlist:
    """Parse an OpenSubsonic playlist payload into an MA Playlist."""
    parsed = Playlist(
        item_id=str(playlist.get("id")),
        provider=instance_id,
        name=_first(playlist.get("name"), playlist.get("title"), "?"),
        owner=str(playlist.get("owner") or ""),
        is_editable=False,
        provider_mappings={
            ProviderMapping(
                item_id=str(playlist.get("id")),
                provider_domain=MF_DOMAIN,
                provider_instance=instance_id,
            )
        },
    )
    _add_image(parsed.metadata, instance_id, playlist.get("coverArt"))
    return parsed


# ----------------------------------------------------------------------------------
# misc
# ----------------------------------------------------------------------------------


def get_item_mapping(instance_id: str, media_type: MediaType, key: str, name: str) -> ItemMapping:
    """Construct an ItemMapping for the specified media."""
    return ItemMapping(
        media_type=media_type,
        item_id=key,
        provider=instance_id,
        name=name,
    )


def parse_structured_lyrics(lyrics: dict[str, Any]) -> tuple[str, bool]:
    """Parse the OpenSubsonic structured lyrics object into MA lyrics."""
    lines: list[str] = []
    synced = bool(lyrics.get("synced"))
    if synced:
        offset: int = _as_int(lyrics.get("offset"))
        for line in lyrics.get("line") or []:
            start = line.get("start")
            if start is None:
                raise InvalidDataError("Open Subsonic synced lyric missing time index")
            ms = _as_int(start) + offset
            dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
            ts = dt.strftime("%M:%S.%f")[:-4]
            lines.append(f"[{ts}]{line.get('value', '')}")
    else:
        for line in lyrics.get("line") or []:
            lines.append(str(line.get("value", "")))
    return ("\n".join(lines), synced)


def to_datetime(value: Any) -> datetime | None:
    """Parse an ISO datetime string into a datetime object (or None)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
