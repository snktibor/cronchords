-- Playlists can also be synced from YouTube to Spotify, or both ways.

alter table public.playlist_mappings
  add column if not exists direction text not null default 'spotify_to_youtube'
    check (direction in ('spotify_to_youtube', 'youtube_to_spotify', 'both')),
  -- YouTube playlists have no snapshot id. The worker stores a hash of the
  -- playlist's video ids instead, to skip playlists that haven't changed.
  add column if not exists youtube_snapshot_id text,
  -- Two-way syncs only: the [spotify_track_id, youtube_video_id] pairs that
  -- were on both sides after the last sync. When one half of a pair is gone,
  -- the song was removed on that side, and the worker removes the other half.
  add column if not exists synced_songs jsonb not null default '[]';

-- YouTube video -> Spotify track lookup shared by every mapping, so a video
-- is searched on Spotify only once. A null spotify_track_id means the search
-- found nothing usable; the worker tries those again after a while.
create table if not exists public.video_sync_cache (
  youtube_video_id text primary key,
  spotify_track_id text,
  isrc text,
  searched_at timestamptz not null default now()
);

-- Videos found for Spotify tracks are looked up the other way round too.
create index if not exists track_sync_cache_youtube_video_id_idx
  on public.track_sync_cache (youtube_video_id);

revoke all on public.video_sync_cache from anon, authenticated;
grant select, insert, update, delete on public.video_sync_cache to authenticated, service_role;

alter table public.video_sync_cache enable row level security;

drop policy if exists "Owner can manage video cache" on public.video_sync_cache;
create policy "Owner can manage video cache" on public.video_sync_cache
  for all to authenticated
  using ((select private.is_owner()))
  with check ((select private.is_owner()));

-- A playlist that syncs both ways can't be part of another mapping. The
-- mappings would pass on each other's changes, and removing a song from one
-- playlist would remove it from all of them. Changing the direction of a
-- mapping starts its sync state afresh.
create or replace function private.check_playlist_mapping()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if exists (
    select 1
    from public.playlist_mappings other
    where other.id <> new.id
      and (other.direction = 'both' or new.direction = 'both')
      and (
        other.spotify_playlist_id = new.spotify_playlist_id
        or other.youtube_playlist_id = new.youtube_playlist_id
      )
  ) then
    raise exception 'A playlist that syncs both ways can''t be part of another sync.';
  end if;

  if tg_op = 'UPDATE' and new.direction is distinct from old.direction then
    new.spotify_snapshot_id := null;
    new.youtube_snapshot_id := null;
    new.synced_songs := '[]';
  end if;
  return new;
end;
$$;

drop trigger if exists check_playlist_mapping on public.playlist_mappings;
create trigger check_playlist_mapping
  before insert or update of spotify_playlist_id, youtube_playlist_id, direction
  on public.playlist_mappings
  for each row execute function private.check_playlist_mapping();
