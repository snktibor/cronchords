import { useState, type FormEvent } from 'react'
import { saveConfig, type AppConfig } from '../lib/db'
import { redirectUri } from '../lib/oauth'
import { connectSpotify } from '../lib/spotify'
import { errorMessage, formatDate } from '../lib/util'
import { connectYouTube } from '../lib/youtube'
import { Status } from './Status'

// Spotify refresh tokens stop working six months after connecting.
const SPOTIFY_ACCESS_DAYS = 180

export function Accounts({ config }: { config: AppConfig }) {
  const [spotifyClientId, setSpotifyClientId] = useState(config.spotify_client_id ?? '')
  const [googleClientId, setGoogleClientId] = useState(config.google_client_id ?? '')
  const [googleClientSecret, setGoogleClientSecret] = useState(config.google_client_secret ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Saves the credentials, then leaves the page for the provider's consent screen.
  async function connect(event: FormEvent, action: () => Promise<void>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(errorMessage(e))
      setBusy(false)
    }
  }

  const spotifyRenewal = config.spotify_connected_at
    ? new Date(Date.parse(config.spotify_connected_at) + SPOTIFY_ACCESS_DAYS * 86_400_000)
    : null

  return (
    <section>
      <h2>Accounts</h2>
      <p className="note">
        Register this address as the redirect URI at Spotify and Google:{' '}
        <code>{redirectUri()}</code>
      </p>

      <div className="columns">
        <form
          onSubmit={(e) =>
            connect(e, async () => {
              const clientId = spotifyClientId.trim()
              await saveConfig({ spotify_client_id: clientId })
              await connectSpotify(clientId)
            })
          }
        >
          <h3 className="spotify">
            Spotify
            <Connection connected={Boolean(config.spotify_refresh_token)} />
          </h3>
          <label>
            <span>Client ID</span>
            <input
              value={spotifyClientId}
              onChange={(e) => setSpotifyClientId(e.target.value)}
              required
            />
          </label>
          <button disabled={busy}>
            {config.spotify_refresh_token ? 'Reconnect Spotify' : 'Connect Spotify'}
          </button>
          {spotifyRenewal && (
            <p className="note">
              Spotify access lasts six months. Reconnect by{' '}
              {spotifyRenewal.toLocaleDateString(undefined, { dateStyle: 'medium' })}
            </p>
          )}
        </form>

        <form
          onSubmit={(e) =>
            connect(e, async () => {
              const clientId = googleClientId.trim()
              await saveConfig({
                google_client_id: clientId,
                google_client_secret: googleClientSecret.trim(),
              })
              await connectYouTube(clientId)
            })
          }
        >
          <h3 className="youtube">
            YouTube
            <Connection connected={Boolean(config.youtube_refresh_token)} />
          </h3>
          <label>
            <span>Google client ID</span>
            <input
              value={googleClientId}
              onChange={(e) => setGoogleClientId(e.target.value)}
              required
            />
          </label>
          <label>
            <span>Google client secret</span>
            <input
              type="password"
              value={googleClientSecret}
              onChange={(e) => setGoogleClientSecret(e.target.value)}
              required
            />
          </label>
          <button disabled={busy}>
            {config.youtube_refresh_token ? 'Reconnect YouTube' : 'Connect YouTube'}
          </button>
          {config.youtube_connected_at && (
            <p className="note">Connected on {formatDate(config.youtube_connected_at)}</p>
          )}
        </form>
      </div>

      {error && (
        <Status tone="problem" role="alert">
          {error}
        </Status>
      )}
    </section>
  )
}

function Connection({ connected }: { connected: boolean }) {
  return connected ? (
    <Status tone="good">Connected</Status>
  ) : (
    <Status tone="info">Not connected</Status>
  )
}
