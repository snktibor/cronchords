import { supabase } from './supabase'

export interface AppConfig {
  spotify_client_id: string | null
  spotify_refresh_token: string | null
  spotify_connected_at: string | null
  google_client_id: string | null
  google_client_secret: string | null
  youtube_refresh_token: string | null
  youtube_connected_at: string | null
}

export interface PlaylistMapping {
  id: string
  spotify_playlist_id: string
  spotify_playlist_name: string | null
  youtube_playlist_id: string
  youtube_playlist_name: string | null
  last_synced_at: string | null
  last_error: string | null
}

export type NewMapping = Pick<
  PlaylistMapping,
  'spotify_playlist_id' | 'spotify_playlist_name' | 'youtube_playlist_id' | 'youtube_playlist_name'
>

export async function loadConfig(): Promise<AppConfig> {
  const { data, error } = await supabase.from('app_config').select('*').eq('id', 1).maybeSingle()
  if (error) throw new Error(error.message)
  if (!data) {
    throw new Error(
      'No settings found. Run the SQL migration, and sign in as the first user of the Supabase project.',
    )
  }
  return data
}

export async function saveConfig(changes: Partial<AppConfig>): Promise<void> {
  const { data, error } = await supabase.from('app_config').update(changes).eq('id', 1).select('id')
  if (error) throw new Error(error.message)
  if (data.length === 0) throw new Error('Settings could not be saved.')
}

export async function listMappings(): Promise<PlaylistMapping[]> {
  const { data, error } = await supabase
    .from('playlist_mappings')
    .select(
      'id, spotify_playlist_id, spotify_playlist_name, youtube_playlist_id, youtube_playlist_name, last_synced_at, last_error',
    )
    .order('created_at')
  if (error) throw new Error(error.message)
  return data
}

export async function addMapping(mapping: NewMapping): Promise<void> {
  const { error } = await supabase.from('playlist_mappings').insert(mapping)
  if (error?.code === '23505') throw new Error('These two playlists are already synced.')
  if (error) throw new Error(error.message)
}

export async function removeMapping(id: string): Promise<void> {
  const { error } = await supabase.from('playlist_mappings').delete().eq('id', id)
  if (error) throw new Error(error.message)
}
