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
