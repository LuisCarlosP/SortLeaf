-- Fresh installation on Supabase. Auth owns identities and refresh tokens.
create schema if not exists sortleaf_private;
revoke all on schema sortleaf_private from public, anon, authenticated;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text, last_name text, telephone text, profile_picture text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create function sortleaf_private.sync_profile() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if new.is_anonymous is not true then
    insert into public.profiles (id, name, last_name, telephone, profile_picture)
    values (new.id, new.raw_user_meta_data->>'name', new.raw_user_meta_data->>'last_name',
            new.raw_user_meta_data->>'telephone', new.raw_user_meta_data->>'profile_picture')
    on conflict (id) do nothing;
  end if;
  return new;
end $$;
revoke all on function sortleaf_private.sync_profile() from public, anon, authenticated;
create trigger sync_sortleaf_profile after insert or update of is_anonymous on auth.users
for each row execute function sortleaf_private.sync_profile();

create function sortleaf_private.touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at := now();
  return new;
end $$;
create trigger touch_profile before update on public.profiles
for each row execute function sortleaf_private.touch_updated_at();

alter table public.profiles enable row level security;
revoke all on public.profiles from public, anon, authenticated;
grant select, update(name, last_name, telephone, profile_picture) on public.profiles to authenticated;
create policy own_profile on public.profiles to authenticated
using (id = (select auth.uid()) and ((select auth.jwt())->>'is_anonymous') = 'false')
with check (id = (select auth.uid()) and ((select auth.jwt())->>'is_anonymous') = 'false');

insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
values ('avatars','avatars',true,5242880,array['image/jpeg','image/png','image/gif','image/webp']);
create policy own_avatar on storage.objects for all to authenticated
using (bucket_id = 'avatars' and (storage.foldername(name))[1] = (select auth.uid())::text
       and ((select auth.jwt())->>'is_anonymous') = 'false')
with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = (select auth.uid())::text
       and ((select auth.jwt())->>'is_anonymous') = 'false');
