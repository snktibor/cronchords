-- Where a cached match comes from: the worker's own search, or a person who
-- corrected it. Matches can be shared with other cronchords users (see
-- "Sharing matches" in the README), and corrections count for more there,
-- since a person picked them.

alter table public.track_sync_cache
  add column if not exists source text not null default 'search'
    check (source in ('search', 'correction'));

alter table public.video_sync_cache
  add column if not exists source text not null default 'search'
    check (source in ('search', 'correction'));

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
