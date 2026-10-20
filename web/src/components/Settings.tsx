import { useState, type FormEvent } from 'react'
import { saveConfig, type AppConfig } from '../lib/db'
import { redirectUri } from '../lib/oauth'
import { connectSpotify } from '../lib/spotify'
import { errorMessage, formatDate } from '../lib/util'
import { connectYouTube } from '../lib/youtube'

// Spotify refresh tokens stop working six months after connecting.
const SPOTIFY_ACCESS_DAYS = 180

export function Settings({ config }: { config: AppConfig }) {
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

  const spotifyExpiry = config.spotify_connected_at
    ? new Date(Date.parse(config.spotify_connected_at) + SPOTIFY_ACCESS_DAYS * 86_400_000)
    : null

  return (
    <section>
      <h2>Settings</h2>
      <p>
        Redirect URI to register with Spotify and Google: <code>{redirectUri()}</code>
      </p>

      <form
        onSubmit={(e) =>
          connect(e, async () => {
            const clientId = spotifyClientId.trim()
            await saveConfig({ spotify_client_id: clientId })
            await connectSpotify(clientId)
          })
        }
      >
        <h3>
          Spotify <Status connected={Boolean(config.spotify_refresh_token)} />
        </h3>
        <label>
          Client ID
          <input
            value={spotifyClientId}
            onChange={(e) => setSpotifyClientId(e.target.value)}
            required
          />
        </label>
        <button disabled={busy}>
          {config.spotify_refresh_token ? 'Reconnect Spotify' : 'Connect Spotify'}
        </button>
        {spotifyExpiry && (
          <p>
            Spotify access has to be renewed every six months, next time around{' '}
            {spotifyExpiry.toLocaleDateString()}.
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
        <h3>
          YouTube <Status connected={Boolean(config.youtube_refresh_token)} />
        </h3>
        <label>
          Google client ID
          <input
            value={googleClientId}
            onChange={(e) => setGoogleClientId(e.target.value)}
            required
          />
        </label>
        <label>
          Google client secret
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
          <p>Connected on {formatDate(config.youtube_connected_at)}.</p>
        )}
      </form>

      {error && <p role="alert">{error}</p>}
    </section>
  )
}

function Status({ connected }: { connected: boolean }) {
  return connected ? (
    <span className="ok">✓ connected</span>
  ) : (
    <span className="missing">not connected</span>
  )
}
