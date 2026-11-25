create table public.activity_events (
  owner_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  id uuid not null default gen_random_uuid(),
  materials text[] not null check (cardinality(materials) between 1 and 8 and octet_length(materials::text) <= 512),
  confidence real check (confidence between 0 and 1),
  recyclable boolean,
  created_at timestamptz not null default now(),
  primary key(owner_id, id)
);
create index activity_owner_recent on public.activity_events(owner_id, created_at desc, id desc);
alter table public.activity_events enable row level security;
revoke all on public.activity_events from public, anon, authenticated;
grant select, delete on public.activity_events to authenticated;
create policy permanent_account_activity on public.activity_events to authenticated
using (owner_id = (select auth.uid()) and ((select auth.jwt())->>'is_anonymous') = 'false')
with check (owner_id = (select auth.uid()) and ((select auth.jwt())->>'is_anonymous') = 'false');
