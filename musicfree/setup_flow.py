"""Setup flow for the MusicFree provider."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType

from music_assistant.constants import CONF_PASSWORD, CONF_PATH, CONF_PORT, CONF_USERNAME
from music_assistant.models.setup_flow import SetupFlowError
from music_assistant.providers.musicfree.sonic_provider import CONF_BASE_URL

if TYPE_CHECKING:
    from music_assistant.models.setup_flow import SetupSession

_ENTRIES = (
    ConfigEntry(
        key=CONF_USERNAME,
        type=ConfigEntryType.STRING,
        required=True,
        label="Username",
        description="Your username for the MusicFree server.",
    ),
    ConfigEntry(
        key=CONF_PASSWORD,
        type=ConfigEntryType.SECURE_STRING,
        required=True,
        label="Password",
        description="The password associated with the username.",
    ),
    ConfigEntry(
        key=CONF_BASE_URL,
        type=ConfigEntryType.STRING,
        required=True,
        label="Base URL",
        description="Base URL for the MusicFree server, e.g. http://192.168.1.100",
    ),
    ConfigEntry(
        key=CONF_PORT,
        type=ConfigEntryType.INTEGER,
        required=False,
        default_value=None,
        label="Port",
        description="Port number for the MusicFree server (usually 80 for HTTP, 443 for HTTPS).",
    ),
    ConfigEntry(
        key=CONF_PATH,
        type=ConfigEntryType.STRING,
        required=False,
        default_value="",
        label="Server Path",
        description="Path to append to the base URL, e.g. /rest. Usually empty unless behind a proxy.",
    ),
)


async def run_setup(session: SetupSession) -> None:
    """Run the setup flow: collect the connection details and create the provider."""
    errors: dict[str, str] | None = None
    setup_data = dict(session.context.setup_data)
    while True:
        entries = [
            replace(entry, value=setup_data.get(entry.key, entry.value)) for entry in _ENTRIES
        ]
        submitted = await session.form(entries, step_id="user", errors=errors, last_step=True)
        setup_data.update(submitted)
        try:
            await session.finish(setup_data)
            return
        except SetupFlowError as err:
            errors = {"base": err.translation_key or str(err)}
