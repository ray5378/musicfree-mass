"""Setup flow for the MusicFree provider."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType

if TYPE_CHECKING:
    from music_assistant.models.setup_flow import SetupSession

CONF_PORT = "port"
CONF_TOKEN = "token"
CONF_SEARCH_SCOPE = "search_scope"

_ENTRIES = (
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
        # Auto-generate token if not provided
        if not setup_data.get(CONF_TOKEN):
            import secrets

            setup_data[CONF_TOKEN] = secrets.token_hex(32)
        try:
            await session.finish(setup_data)
            return
        except Exception as err:
            errors = {"base": str(err)}
