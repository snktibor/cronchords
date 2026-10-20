import { useEffect, useState, type FormEvent } from 'react'
import { addMapping } from '../lib/db'
import { listSpotifyPlaylists } from '../lib/spotify'
import { errorMessage, type Playlist } from '../lib/util'
import { createYouTubePlaylist, listYouTubePlaylists } from '../lib/youtube'

const NEW_PLAYLIST = 'new'

export function NewMapping({ onCreated }: { onCreated: () => void }) {
  const [spotifyPlaylists, setSpotifyPlaylists] = useState<Playlist[] | null>(null)
  const [youtubePlaylists, setYouTubePlaylists] = useState<Playlist[] | null>(null)
  const [spotifyId, setSpotifyId] = useState('')
  const [youtubeId, setYouTubeId] = useState('')
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

  async function submit(event: FormEvent) {
    event.preventDefault()
    const source = spotifyPlaylists?.find((p) => p.id === spotifyId)
    let target = youtubePlaylists?.find((p) => p.id === youtubeId)
    if (!source) return
    setBusy(true)
    setError(null)
    try {
      if (youtubeId === NEW_PLAYLIST) {
        const created = await createYouTubePlaylist(source.name)
        setYouTubePlaylists((playlists) => [created, ...(playlists ?? [])])
        target = created
      }
      if (!target) return
      await addMapping({
        spotify_playlist_id: source.id,
        spotify_playlist_name: source.name,
        youtube_playlist_id: target.id,
        youtube_playlist_name: target.name,
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
            {spotifyPlaylists?.map((playlist) => (
              <option key={playlist.id} value={playlist.id}>
                {playlist.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          YouTube playlist
          <select value={youtubeId} onChange={(e) => setYouTubeId(e.target.value)} required>
            <option value="">{youtubePlaylists ? 'Choose a playlist' : 'Loading…'}</option>
            <option value={NEW_PLAYLIST}>New private playlist with the same name</option>
            {youtubePlaylists?.map((playlist) => (
              <option key={playlist.id} value={playlist.id}>
                {playlist.name}
              </option>
            ))}
          </select>
        </label>
        <button disabled={busy}>Create sync</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
