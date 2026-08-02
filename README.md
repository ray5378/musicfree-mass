# MusicFree Provider for Music Assistant

A [Music Assistant](https://music-assistant.io) third-party music source that connects to a
[music-free-site](https://github.com/ray5378/music-free-site) server through its
OpenSubsonic API (v1.16.1) and imports its library into Music Assistant.

Unlike the built-in OpenSubsonic provider, this plugin is tuned for the
music-free-site server: it uses `timeOffset`-based seeking (the server ignores
HTTP `Range` requests), resolves cover art with `so-`/`al-`/`ar-` prefixed ids,
and maps the server's LRC lyrics, playlists and recommendations.

## Features

- Browse, search and stream the full library of a music-free-site server
- Library sync of artists, albums, tracks and playlists into Music Assistant
- Cover art resolution (`getCoverArt`)
- Synced / plain lyrics (`getLyricsBySongId`, `getLyrics`)
- Recommendations: starred items, new albums, most played albums, random songs
- Full seeking support via `timeOffset` transcoding streams
- Multi-instance: connect to several servers at once
- No external dependencies (pure `aiohttp`)

## Installation

### Prerequisites

- [Music Assistant](https://music-assistant.io) server installed and running
- Python 3.14+
- A running [music-free-site](https://github.com/ray5378/music-free-site) server
  with OpenSubsonic credentials

### Manual Installation

1. Clone this repository:

```bash
git clone https://github.com/ray5378/musicfree-mass.git
```

2. Copy the `musicfree` directory into the Music Assistant providers folder
   (or mount it, e.g. with Docker):

```bash
cp -r musicfree-mass/musicfree /path/to/music_assistant/providers/musicfree
```

3. Restart Music Assistant server

4. Go to Settings → Providers → Add Provider → MusicFree Server

5. Enter the server address, port, username and password

### Third-party Provider Source

If your Music Assistant instance supports third-party provider sources, add
this repository URL as a provider source.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| Base URL / IP Address | – | Address of your MusicFree server, e.g. `http://192.168.1.10` |
| Port | – | Port the MusicFree server listens on |
| Server Path | – | Optional sub path (e.g. `/subsonic`), usually empty |
| Username | – | Username for the MusicFree server |
| Password | – | Password for the MusicFree server |
| Recommendation Limit | 10 | How many items per recommendation type |
| Items per request (advanced) | 200 | Page size when enumerating the library (max 500) |

## OpenSubsonic endpoints used

The provider consumes the following music-free-site OpenSubsonic endpoints:

- `ping`, `getLicense` - connectivity + auth checks
- `getArtists`, `getAlbumList2`, `getRandomSongs` - library enumeration
- `getArtist`, `getAlbum`, `getSong`, `getPlaylist` - item lookups
- `search3` - search
- `getArtistInfo2` - top tracks for an artist
- `getSimilarSongs2` - similar tracks
- `getStarred` - favorites / starred recommendations
- `getPlaylists` - playlist list
- `getLyricsBySongId`, `getLyrics` - lyrics
- `getCoverArt` - artwork
- `stream` - audio streaming (with `timeOffset` for seeking)
- `star` - toggling favorites
- `scrobble` - playback reporting

## License

Apache License 2.0
