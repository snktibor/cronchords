import { useEffect, useState, type FormEvent } from 'react'
import { addMapping, type SyncDirection } from '../lib/db'
import { createSpotifyPlaylist, listSpotifyPlaylists } from '../lib/spotify'
import { DIRECTIONS, errorMessage, type Playlist } from '../lib/util'
import { createYouTubePlaylist, listYouTubePlaylists } from '../lib/youtube'

const NEW_PLAYLIST = 'new'

export function NewMapping({ onCreated }: { onCreated: () => void }) {
  const [spotifyPlaylists, setSpotifyPlaylists] = useState<Playlist[] | null>(null)
  const [youtubePlaylists, setYouTubePlaylists] = useState<Playlist[] | null>(null)
  const [spotifyId, setSpotifyId] = useState('')
  const [youtubeId, setYouTubeId] = useState('')
  const [direction, setDirection] = useState<SyncDirection>('both')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listSpotifyPlaylists().then(setSpotifyPlaylists, (e) => {
      setSpotifyPlaylists([])
      setError(errorMessage(e))
    })
    listYouTubePlaylists().then(setYouTubePlaylists, (e) => {
      setYouTubePlaylists([])
      setError(errorMessage(e))
    })
  }, [])

  // A new, empty playlist only makes sense on a side that receives songs.
  const newOnSpotify = direction !== 'spotify_to_youtube'
  const newOnYouTube = direction !== 'youtube_to_spotify'

  function changeDirection(value: SyncDirection) {
    setDirection(value)
    if (value === 'spotify_to_youtube' && spotifyId === NEW_PLAYLIST) setSpotifyId('')
    if (value === 'youtube_to_spotify' && youtubeId === NEW_PLAYLIST) setYouTubeId('')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (spotifyId === NEW_PLAYLIST && youtubeId === NEW_PLAYLIST) {
      setError('Choose an existing playlist on one of the two sides.')
      return
    }
    let spotify = spotifyPlaylists?.find((p) => p.id === spotifyId)
    let youtube = youtubePlaylists?.find((p) => p.id === youtubeId)
    setBusy(true)
    setError(null)
    try {
      if (spotifyId === NEW_PLAYLIST && youtube) {
        const created = await createSpotifyPlaylist(youtube.name)
        setSpotifyPlaylists((playlists) => [created, ...(playlists ?? [])])
        spotify = created
      }
      if (youtubeId === NEW_PLAYLIST && spotify) {
        const created = await createYouTubePlaylist(spotify.name)
        setYouTubePlaylists((playlists) => [created, ...(playlists ?? [])])
        youtube = created
      }
      if (!spotify || !youtube) return
      await addMapping({
        spotify_playlist_id: spotify.id,
        spotify_playlist_name: spotify.name,
        youtube_playlist_id: youtube.id,
        youtube_playlist_name: youtube.name,
        direction,
      })
      setSpotifyId('')
      setYouTubeId('')
      onCreated()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>New sync</h2>
      <form onSubmit={submit}>
        <label>
          Spotify playlist
          <select value={spotifyId} onChange={(e) => setSpotifyId(e.target.value)} required>
            <option value="">{spotifyPlaylists ? 'Choose a playlist' : 'Loading…'}</option>
            {newOnSpotify && (
              <option value={NEW_PLAYLIST}>New private playlist with the same name</option>
            )}
            {spotifyPlaylists?.map((playlist) => (
              <option key={playlist.id} value={playlist.id}>
                {playlist.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Direction
          <select
            value={direction}
            onChange={(e) => changeDirection(e.target.value as SyncDirection)}
          >
            {Object.entries(DIRECTIONS).map(([value, { label }]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          YouTube playlist
          <select value={youtubeId} onChange={(e) => setYouTubeId(e.target.value)} required>
            <option value="">{youtubePlaylists ? 'Choose a playlist' : 'Loading…'}</option>
            {newOnYouTube && (
              <option value={NEW_PLAYLIST}>New private playlist with the same name</option>
            )}
            {youtubePlaylists?.map((playlist) => (
              <option key={playlist.id} value={playlist.id}>
                {playlist.name}
              </option>
            ))}
          </select>
        </label>
        {direction === 'both' && (
          <p>
            Songs added on either side are copied to the other one, and songs removed on either
            side are removed from the other one too.
          </p>
        )}
        <button disabled={busy}>Create sync</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
