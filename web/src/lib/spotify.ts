import { loadConfig, saveConfig } from './db'
import {
  authorize,
  redirectUri,
  rememberAccessToken,
  rememberedAccessToken,
  requestToken,
  TokenError,
} from './oauth'
import type { Playlist } from './util'

const AUTHORIZE_URL = 'https://accounts.spotify.com/authorize'
const TOKEN_URL = 'https://accounts.spotify.com/api/token'
const API_URL = 'https://api.spotify.com/v1'
const SCOPES = [
  'playlist-read-private',
  'playlist-read-collaborative',
  'playlist-modify-private',
  'playlist-modify-public',
].join(' ')

interface Page<T> {
  items: (T | null)[]
  next: string | null
}

interface SpotifyPlaylist {
  id: string
  name: string
  collaborative: boolean
  owner: { id: string }
}

export function connectSpotify(clientId: string): Promise<void> {
  return authorize('spotify', AUTHORIZE_URL, { client_id: clientId, scope: SCOPES })
}

export async function finishSpotifyConnection(code: string, verifier: string): Promise<void> {
  const { spotify_client_id: clientId } = await loadConfig()
  if (!clientId) throw new Error('Save your Spotify client ID first.')
  const token = await requestToken(TOKEN_URL, {
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirectUri(),
    client_id: clientId,
    code_verifier: verifier,
  })
  await saveConfig({
    spotify_refresh_token: token.refresh_token ?? null,
    spotify_connected_at: new Date().toISOString(),
  })
  rememberAccessToken('spotify', token)
}

/** Playlists the sync can read: since February 2026 only your own and collaborative ones. */
export async function listSpotifyPlaylists(): Promise<Playlist[]> {
  const token = await accessToken()
  const me = await call<{ id: string }>(`${API_URL}/me`, token)
  const playlists: Playlist[] = []
  let url: string | null = `${API_URL}/me/playlists?limit=50`
  while (url) {
    const page: Page<SpotifyPlaylist> = await call(url, token)
    for (const playlist of page.items) {
      if (playlist && (playlist.owner.id === me.id || playlist.collaborative)) {
        playlists.push({ id: playlist.id, name: playlist.name })
      }
    }
    url = page.next
  }
  return playlists
}

export async function createSpotifyPlaylist(name: string): Promise<Playlist> {
  const token = await accessToken()
  const playlist = await call<SpotifyPlaylist>(`${API_URL}/me/playlists`, token, {
    method: 'POST',
    body: JSON.stringify({ name, public: false, description: 'Synced with YouTube by cronchords' }),
  })
  return { id: playlist.id, name: playlist.name }
}

async function accessToken(): Promise<string> {
  const remembered = rememberedAccessToken('spotify')
  if (remembered) return remembered
  try {
    return await refreshAccessToken()
  } catch (error) {
    // The sync worker rotates the refresh token too. If it did so since we
    // read it, the stored one is already the new token, so just try again.
    if (error instanceof TokenError && error.code === 'invalid_grant') return refreshAccessToken()
    throw error
  }
}

async function refreshAccessToken(): Promise<string> {
  const config = await loadConfig()
  if (!config.spotify_client_id || !config.spotify_refresh_token) {
    throw new Error('Spotify is not connected.')
  }
  const token = await requestToken(TOKEN_URL, {
    grant_type: 'refresh_token',
    refresh_token: config.spotify_refresh_token,
    client_id: config.spotify_client_id,
  })
  if (token.refresh_token && token.refresh_token !== config.spotify_refresh_token) {
    await saveConfig({ spotify_refresh_token: token.refresh_token })
  }
  rememberAccessToken('spotify', token)
  return token.access_token
}

async function call<T>(url: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(data?.error?.message ?? `Spotify API error ${response.status}`)
  }
  return response.json()
}
