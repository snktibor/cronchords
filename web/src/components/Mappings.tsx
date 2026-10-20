import { useState } from 'react'
import { removeMapping, type PlaylistMapping } from '../lib/db'
import { errorMessage, formatDate } from '../lib/util'

export function Mappings({
  mappings,
  onChange,
}: {
  mappings: PlaylistMapping[]
  onChange: () => void
}) {
  const [error, setError] = useState<string | null>(null)

  async function remove(id: string) {
    setError(null)
    try {
      await removeMapping(id)
      onChange()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <section>
      <h2>Active syncs</h2>
      {mappings.length === 0 ? (
        <p>Nothing is synced yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Spotify</th>
              <th>YouTube</th>
              <th>Last synced</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {mappings.map((mapping) => (
              <tr key={mapping.id}>
                <td>
                  <a
                    href={`https://open.spotify.com/playlist/${mapping.spotify_playlist_id}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {mapping.spotify_playlist_name ?? mapping.spotify_playlist_id}
                  </a>
                </td>
                <td>
                  <a
                    href={`https://music.youtube.com/playlist?list=${mapping.youtube_playlist_id}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {mapping.youtube_playlist_name ?? mapping.youtube_playlist_id}
                  </a>
                </td>
                <td>
                  {mapping.last_synced_at
                    ? formatDate(mapping.last_synced_at)
                    : 'Waiting for the first run'}
                  {mapping.last_error && <div className="warning">{mapping.last_error}</div>}
                </td>
                <td>
                  <button type="button" onClick={() => remove(mapping.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
