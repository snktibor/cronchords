-- Where a cached match comes from: the worker's own search, a person who
-- corrected it, or the list of matches cronchords users share (see "Sharing
-- matches" in the README). Corrections count for more on that list, since a
-- person picked them, and matches taken from it aren't shared again.

alter table public.track_sync_cache
  add column if not exists source text not null default 'search';
alter table public.track_sync_cache
  drop constraint if exists track_sync_cache_source_check,
  add constraint track_sync_cache_source_check
    check (source in ('search', 'correction', 'shared'));

alter table public.video_sync_cache
  add column if not exists source text not null default 'search';
alter table public.video_sync_cache
  drop constraint if exists video_sync_cache_source_check,
  add constraint video_sync_cache_source_check
    check (source in ('search', 'correction', 'shared'));

-- The worker says itself where a match comes from: it also saves the
-- replacements people make in two-way syncs. Anyone else writing a row, in the
-- table editor say, is correcting a match by hand.
create or replace function private.mark_corrected_match()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if current_user <> 'service_role' then
    new.source := 'correction';
  end if;
  return new;
end;
$$;

drop trigger if exists mark_corrected_match on public.track_sync_cache;
create trigger mark_corrected_match
  before insert or update on public.track_sync_cache
  for each row execute function private.mark_corrected_match();

drop trigger if exists mark_corrected_match on public.video_sync_cache;
create trigger mark_corrected_match
  before insert or update on public.video_sync_cache
  for each row execute function private.mark_corrected_match();
