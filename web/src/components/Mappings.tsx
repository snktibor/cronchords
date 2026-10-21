import { useState } from 'react'
import { removeMapping, type PlaylistMapping } from '../lib/db'
import { DIRECTIONS, errorMessage, formatDate } from '../lib/util'
import { Status } from './Status'

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
        <p className="lead">Nothing is synced yet.</p>
      ) : (
        <div>
          <div className="pairs-head">
            <span className="spotify">Spotify</span>
            <span />
            <span className="youtube">YouTube</span>
          </div>
          <ul>
            {mappings.map((mapping) => (
              <Pair key={mapping.id} mapping={mapping} onRemove={() => remove(mapping.id)} />
            ))}
          </ul>
        </div>
      )}
      {error && (
        <Status tone="problem" role="alert">
          {error}
        </Status>
      )}
    </section>
  )
}

function Pair({ mapping, onRemove }: { mapping: PlaylistMapping; onRemove: () => void }) {
  const direction = DIRECTIONS[mapping.direction]
  return (
    <li className="pair">
      <a
        href={`https://open.spotify.com/playlist/${mapping.spotify_playlist_id}`}
        target="_blank"
        rel="noreferrer"
      >
        {mapping.spotify_playlist_name ?? mapping.spotify_playlist_id}
      </a>
      <span className="pair-arrow" title={direction.label}>
        <span aria-hidden="true">{direction.arrow}</span>
        <span className="visually-hidden">{direction.label}</span>
      </span>
      <a
        href={`https://music.youtube.com/playlist?list=${mapping.youtube_playlist_id}`}
        target="_blank"
        rel="noreferrer"
      >
        {mapping.youtube_playlist_name ?? mapping.youtube_playlist_id}
      </a>
      <p className="pair-meta">
        <span>
          {mapping.last_synced_at
            ? `Synced ${formatDate(mapping.last_synced_at)}`
            : 'Waiting for the first run'}
        </span>
        <button type="button" className="quiet" onClick={onRemove}>
          Stop syncing
        </button>
      </p>
      {mapping.last_error && <Status tone="warning">{mapping.last_error}</Status>}
    </li>
  )
}
