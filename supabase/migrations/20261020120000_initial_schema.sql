-- Access model:
--   * The sync worker uses the project's secret key (service_role), which
--     bypasses row level security.
--   * The dashboard uses the publishable key and signs in with Supabase Auth.
--     Only the owner, i.e. the first user created in Authentication > Users,
--     gets through the policies below. Anyone else, signed in or not, sees
--     nothing, even if sign-ups are left enabled.

create schema if not exists private;

create or replace function private.is_owner()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    auth.uid() = (select id from auth.users order by created_at, id limit 1),
    false
  );
$$;

revoke all on function private.is_owner() from public;
grant usage on schema private to authenticated;
grant execute on function private.is_owner() to authenticated;

-- A single row with the credentials the worker needs.
create table if not exists public.app_config (
  id smallint primary key default 1 check (id = 1),
  spotify_client_id text,
  spotify_refresh_token text,
  spotify_connected_at timestamptz,
  google_client_id text,
  google_client_secret text,
  youtube_refresh_token text,
  youtube_connected_at timestamptz
);

insert into public.app_config (id) values (1) on conflict (id) do nothing;

-- One row per Spotify playlist -> YouTube playlist pair. spotify_snapshot_id
-- is the playlist version that was last synced completely; last_synced_at is
-- the last time the worker confirmed the pair was in sync.
create table if not exists public.playlist_mappings (
  id uuid primary key default gen_random_uuid(),
  spotify_playlist_id text not null,
  spotify_playlist_name text,
  youtube_playlist_id text not null,
  youtube_playlist_name text,
  spotify_snapshot_id text,
  last_synced_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  unique (spotify_playlist_id, youtube_playlist_id)
);

-- ISRC -> YouTube video lookup shared by every mapping, so a song is searched
-- on YouTube only once. A null youtube_video_id means the search found nothing
-- usable; the worker tries those again after a while.
create table if not exists public.track_sync_cache (
  isrc text primary key,
  spotify_track_id text not null,
  youtube_video_id text,
  searched_at timestamptz not null default now()
);

revoke all on public.app_config, public.playlist_mappings, public.track_sync_cache
  from anon, authenticated;

grant select, insert, update on public.app_config to authenticated;
grant select, insert, update, delete on public.playlist_mappings, public.track_sync_cache
  to authenticated;
grant select, insert, update, delete
  on public.app_config, public.playlist_mappings, public.track_sync_cache
  to service_role;

alter table public.app_config enable row level security;
alter table public.playlist_mappings enable row level security;
alter table public.track_sync_cache enable row level security;

drop policy if exists "Owner can manage config" on public.app_config;
create policy "Owner can manage config" on public.app_config
  for all to authenticated
  using ((select private.is_owner()))
  with check ((select private.is_owner()));

drop policy if exists "Owner can manage mappings" on public.playlist_mappings;
create policy "Owner can manage mappings" on public.playlist_mappings
  for all to authenticated
  using ((select private.is_owner()))
  with check ((select private.is_owner()));

drop policy if exists "Owner can manage track cache" on public.track_sync_cache;
create policy "Owner can manage track cache" on public.track_sync_cache
  for all to authenticated
  using ((select private.is_owner()))
  with check ((select private.is_owner()));
