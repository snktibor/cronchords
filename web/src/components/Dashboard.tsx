import { useCallback, useEffect, useRef, useState } from 'react'
import { listMappings, loadConfig, type AppConfig, type PlaylistMapping } from '../lib/db'
import { forgetAccessToken, takeAuthorizationResult } from '../lib/oauth'
import { finishSpotifyConnection } from '../lib/spotify'
import { supabase } from '../lib/supabase'
import { errorMessage } from '../lib/util'
import { finishYouTubeConnection } from '../lib/youtube'
import { Accounts } from './Accounts'
import { LogOutIcon } from './icons'
import { Mappings } from './Mappings'
import { NewMapping } from './NewMapping'
import { Status } from './Status'

export function Dashboard({ email }: { email: string | undefined }) {
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [mappings, setMappings] = useState<PlaylistMapping[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const started = useRef(false)

  const reload = useCallback(async () => {
    try {
      const [config, mappings] = await Promise.all([loadConfig(), listMappings()])
      setConfig(config)
      setMappings(mappings)
    } catch (e) {
      setError(errorMessage(e))
    }
  }, [])

  useEffect(() => {
    // Only once, also under StrictMode: an authorization code can't be used twice.
    if (started.current) return
    started.current = true
    finishAuthorization()
      .then((connected) => {
        if (connected) setNotice(`${connected} is connected.`)
      })
      .catch((e) => setError(errorMessage(e)))
      .finally(reload)
  }, [reload])

  async function signOut() {
    forgetAccessToken('spotify')
    forgetAccessToken('youtube')
    await supabase.auth.signOut()
  }

  const connected = Boolean(config?.spotify_refresh_token && config?.youtube_refresh_token)

  return (
    <main className="page">
      <header className="top">
        <h1 className="wordmark">cronchords</h1>
        <div className="tools">
          <span>{email}</span>
          <button type="button" className="quiet" onClick={signOut}>
            <LogOutIcon />
            Sign out
          </button>
        </div>
      </header>
      {notice && (
        <Status tone="good" role="status">
          {notice}
        </Status>
      )}
      {error && (
        <Status tone="problem" role="alert">
          {error}
        </Status>
      )}
      {config && (
        <div className="sections">
          <Accounts config={config} />
          {connected ? (
            <NewMapping onCreated={reload} />
          ) : (
            <section>
              <h2>New sync</h2>
              <p className="lead">Connect Spotify and YouTube to start syncing playlists.</p>
            </section>
          )}
          <Mappings mappings={mappings} onChange={reload} />
        </div>
      )}
    </main>
  )
}

async function finishAuthorization(): Promise<string | null> {
  const result = takeAuthorizationResult()
  if (!result) return null
  if (result.provider === 'spotify') {
    await finishSpotifyConnection(result.code, result.verifier)
    return 'Spotify'
  }
  await finishYouTubeConnection(result.code, result.verifier)
  return 'YouTube'
}
