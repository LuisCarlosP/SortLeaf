create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  language text not null check (language in ('es','en')),
  title text not null default '' check (length(title) <= 65),
  material jsonb check (octet_length(material::text) <= 12000),
  revision bigint not null default 0,
  updated_at timestamptz not null default now()
);
create index conversations_owner_recent on public.conversations(owner_id, updated_at desc, id);
create table public.chat_turns (
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  revision bigint not null,
  request_id uuid,
  unique (conversation_id, request_id),
  prompt text not null check (length(prompt) <= 16000),
  question text not null check (length(question) <= 1000),
  answer text not null check (length(answer) <= 32000),
  primary key (conversation_id, revision)
);
alter table public.conversations enable row level security;
alter table public.chat_turns enable row level security;
revoke all on public.conversations, public.chat_turns from public, anon, authenticated;
grant select, delete on public.conversations to authenticated;
grant select on public.chat_turns to authenticated;
create policy own_chats on public.conversations to authenticated
using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy own_turns on public.chat_turns for select to authenticated
using (exists(select 1 from public.conversations c where c.id = conversation_id));
