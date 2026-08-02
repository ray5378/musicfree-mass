"""MusicFree (music-free-site) music provider support for MusicAssistant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry, ConfigValueType
from music_assistant_models.enums import ConfigEntryType, ProviderFeature

from .constants import (
    MF_CONF_BASE_URL,
    MF_CONF_PASSWORD,
    MF_CONF_PATH,
    MF_CONF_PAGE_SIZE,
    MF_CONF_PORT,
    MF_CONF_RECO_SIZE,
    MF_CONF_USERNAME,
)
from .provider import MusicFreeProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

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


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return MusicFreeProvider(mass, manifest, config, SUPPORTED_FEATURES)


async def get_config_entries(
    mass: MusicAssistant,  # noqa: ARG001
    instance_id: str | None = None,  # noqa: ARG001
    action: str | None = None,  # noqa: ARG001
    values: dict[str, ConfigValueType] | None = None,  # noqa: ARG001
) -> tuple[ConfigEntry, ...]:
    """Return Config entries to setup this provider."""
    return (
        ConfigEntry(
            key=MF_CONF_BASE_URL,
            type=ConfigEntryType.STRING,
            label="Base URL / IP Address",
            required=True,
            description="The address of your MusicFree server, e.g. http://192.168.1.10",
        ),
        ConfigEntry(
            key=MF_CONF_PORT,
            type=ConfigEntryType.INTEGER,
            label="Port",
            required=False,
            description="The port your MusicFree server listens on (leave empty for the default)",
        ),
        ConfigEntry(
            key=MF_CONF_PATH,
            type=ConfigEntryType.STRING,
            label="Server Path",
            required=False,
            description="Optional sub path under which the server is served (e.g. /subsonic), "
            "usually left empty",
        ),
        ConfigEntry(
            key=MF_CONF_USERNAME,
            type=ConfigEntryType.STRING,
            label="Username",
            required=True,
            description="Your username for this MusicFree server",
        ),
        ConfigEntry(
            key=MF_CONF_PASSWORD,
            type=ConfigEntryType.SECURE_STRING,
            label="Password",
            required=True,
            description="The password associated with the username",
        ),
        ConfigEntry(
            key=MF_CONF_RECO_SIZE,
            type=ConfigEntryType.INTEGER,
            label="Recommendation Limit",
            required=True,
            description="How many recommendations from each enabled type should be included.",
            default_value=10,
        ),
        ConfigEntry(
            key=MF_CONF_PAGE_SIZE,
            type=ConfigEntryType.INTEGER,
            label="Number of items included per server request.",
            required=True,
            description="When enumerating items from the server, how many should be in each "
            "request. Smaller will require more requests but is better for low bandwidth "
            "connections. The Open Subsonic spec says the max value for this is 500 items.",
            default_value=200,
            advanced=True,
        ),
    )
