# MusicFree Provider for Music Assistant

OpenSubsonic API bridge plugin for Music Assistant, compatible with [music-free-site](https://github.com/ray5378/music-free-site) and other OpenSubsonic clients.

## Features

- Browse and stream all Music Assistant music sources (local files, Spotify, Tidal, etc.) via the Subsonic protocol
- Full OpenSubsonic API compatibility (v1.16.1)
- Supports JSON and XML response formats
- Multiple authentication methods: X-API-Key, Bearer Token, OpenSubsonic token auth
- Compatible with any Subsonic/OpenSubsonic client

## Installation

### Prerequisites

- [Music Assistant](https://music-assistant.io) server installed and running
- Python 3.14+

### Manual Installation

1. Clone this repository or download the `musicfree` directory:

```bash
git clone https://github.com/ray5378/musicfree-mass.git
```

2. Copy the `musicfree` directory to your Music Assistant providers directory:

```bash
cp -r musicfree-mass/musicfree /path/to/music_assistant/providers/musicfree
```

3. Restart Music Assistant server

4. Go to Settings → Providers → Add Provider → MusicFree

5. Configure the port (default: 4533) and optional API token

### Third-party Provider Source

If you are using a Music Assistant instance that supports third-party provider sources, add this repository URL as a provider source.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| Port | 4533 | Port for the Subsonic API server |
| API Token | Auto-generated | Token for client authentication |
| Search Scope | (empty) | Comma-separated provider domains to search |

## API Endpoints

The plugin exposes the following OpenSubsonic endpoints:

- `ping` - Server health check
- `getLicense` - License information
- `getMusicFolders` - Music library folders
- `getIndexes` / `getArtists` - Artist listing
- `getArtist` - Artist details with albums
- `getAlbum` - Album details with tracks
- `getSong` - Track details
- `getAlbumList` / `getAlbumList2` - Album browsing
- `search2` / `search3` - Search
- `getPlaylists` / `getPlaylist` - Playlist access
- `getCoverArt` - Album/artist artwork
- `stream` / `download` - Audio streaming
- `getRandomSongs` - Random track selection
- And more...

## Usage

### With music-free-site

Configure music-free-site to connect to:

```
Server URL: http://your-ma-server:4533
Username: admin
Password: (the API token)
```

### With any Subsonic client

Use the server URL `http://your-ma-server:4533/rest` with the configured API token.

## License

Apache License 2.0