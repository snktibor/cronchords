import { loadConfig, saveConfig } from './db'
import {
  authorize,
  redirectUri,
  rememberAccessToken,
  rememberedAccessToken,
  requestToken,
} from './oauth'
import type { Playlist } from './util'

const AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
const TOKEN_URL = 'https://oauth2.googleapis.com/token'
const API_URL = 'https://www.googleapis.com/youtube/v3'
const SCOPE = 'https://www.googleapis.com/auth/youtube'

interface YouTubePlaylist {
  id: string
  snippet: { title: string }
}

export function connectYouTube(clientId: string): Promise<void> {
  // offline + consent: Google only hands out a refresh token with both.
  return authorize('youtube', AUTHORIZE_URL, {
    client_id: clientId,
    scope: SCOPE,
    access_type: 'offline',
    prompt: 'consent',
  })
}

export async function finishYouTubeConnection(code: string, verifier: string): Promise<void> {
  const config = await loadConfig()
  if (!config.google_client_id || !config.google_client_secret) {
    throw new Error('Save your Google client ID and secret first.')
  }
  const token = await requestToken(TOKEN_URL, {
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirectUri(),
    client_id: config.google_client_id,
    client_secret: config.google_client_secret,
    code_verifier: verifier,
  })
  if (!token.refresh_token) throw new Error('Google did not send a refresh token. Please try again.')
  await saveConfig({
    youtube_refresh_token: token.refresh_token,
    youtube_connected_at: new Date().toISOString(),
  })
  rememberAccessToken('youtube', token)
}

export async function listYouTubePlaylists(): Promise<Playlist[]> {
  const token = await accessToken()
  const playlists: Playlist[] = []
  let pageToken = ''
  do {
    const params = new URLSearchParams({ part: 'snippet', mine: 'true', maxResults: '50' })
    if (pageToken) params.set('pageToken', pageToken)
    const page = await call<{ items: YouTubePlaylist[]; nextPageToken?: string }>(
      `playlists?${params}`,
      token,
    )
    playlists.push(...page.items.map((p) => ({ id: p.id, name: p.snippet.title })))
    pageToken = page.nextPageToken ?? ''
  } while (pageToken)
  return playlists
}

export async function createYouTubePlaylist(title: string): Promise<Playlist> {
  const token = await accessToken()
  const playlist = await call<YouTubePlaylist>('playlists?part=snippet,status', token, {
    method: 'POST',
    body: JSON.stringify({
      snippet: { title, description: 'Synced with Spotify by cronchords' },
      status: { privacyStatus: 'private' },
    }),
  })
  return { id: playlist.id, name: playlist.snippet.title }
}

async function accessToken(): Promise<string> {
  const remembered = rememberedAccessToken('youtube')
  if (remembered) return remembered
  const config = await loadConfig()
  if (!config.google_client_id || !config.google_client_secret || !config.youtube_refresh_token) {
    throw new Error('YouTube is not connected.')
  }
  const token = await requestToken(TOKEN_URL, {
    grant_type: 'refresh_token',
    refresh_token: config.youtube_refresh_token,
    client_id: config.google_client_id,
    client_secret: config.google_client_secret,
  })
  rememberAccessToken('youtube', token)
  return token.access_token
}

async function call<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}/${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error?.message ?? `YouTube API error ${response.status}`)
  return data
}
