"""Constants for the MusicFree (music-free-site) provider."""

from __future__ import annotations

# Configuration entry keys.
# NOTE: deliberately prefixed/unique so we never collide with the values used by
# the built-in opensubsonic provider or any other provider.
MF_CONF_BASE_URL: str = "mf_base_url"
MF_CONF_PORT: str = "mf_port"
MF_CONF_PATH: str = "mf_path"
MF_CONF_USERNAME: str = "mf_username"
MF_CONF_PASSWORD: str = "mf_password"
MF_CONF_RECO_SIZE: str = "mf_reco_size"
MF_CONF_PAGE_SIZE: str = "mf_page_size"

# provider domain (unique id of this provider within Music Assistant)
MF_DOMAIN: str = "musicfree"

# OpenSubsonic protocol constants
MF_PROTOCOL_VERSION: str = "1.16.1"
MF_CLIENT_NAME: str = "MusicAssistantMusicFree"

# used to construct a fake/unknown artist entry when the server does not give us
# an artist id (kept unique to this provider)
MF_UNKNOWN_ARTIST_ID: str = "musicfree_fake_artist_unknown"

# artist id sentinel for the (fake) "various artists" entries
MF_VARIOUS_PREFIX: str = "MUSICFREE-VARIOUS-"

# recommendation row id's (stable across calls/releases)
MF_RECO_STARRED: str = "musicfree_starred"
MF_RECO_NEW_ALBUMS: str = "musicfree_new_albums"
MF_RECO_MOST_PLAYED: str = "musicfree_most_played"
MF_RECO_RANDOM: str = "musicfree_random"
