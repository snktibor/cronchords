import type { SyncDirection } from './db'

export interface Playlist {
  id: string
  name: string
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export const DIRECTIONS: Record<SyncDirection, { label: string; arrow: string }> = {
  both: { label: 'Both ways', arrow: '↔' },
  spotify_to_youtube: { label: 'Spotify → YouTube', arrow: '→' },
  youtube_to_spotify: { label: 'YouTube → Spotify', arrow: '←' },
}
