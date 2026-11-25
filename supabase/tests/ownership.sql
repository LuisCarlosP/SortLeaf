-- Run with psql on a disposable Supabase database, after applying migrations.
begin;
insert into auth.users (id,is_anonymous,raw_user_meta_data) values
 ('00000000-0000-4000-8000-000000000001', true, '{}'),
 ('00000000-0000-4000-8000-000000000002', true, '{}'),
 ('00000000-0000-4000-8000-000000000003', false, '{}'),
 ('00000000-0000-4000-8000-000000000004', false, '{}');
set local role service_role;
do $$ declare
  guest constant uuid := '00000000-0000-4000-8000-000000000001';
  other_guest constant uuid := '00000000-0000-4000-8000-000000000002';
  member constant uuid := '00000000-0000-4000-8000-000000000003';
  guest_chat constant uuid := '10000000-0000-4000-8000-000000000001';
  member_chat constant uuid := '10000000-0000-4000-8000-000000000003';
begin
  assert public.open_private_chat(guest,guest_chat,'es',null);
  assert not public.open_private_chat(other_guest,guest_chat,'es',null);
  assert public.save_chat_change(guest,guest_chat,0,null,'hola','hola','respuesta','{"materials":["glass"]}');
  assert not public.save_chat_change(other_guest,guest_chat,1,null,'hola','hola','respuesta',null);
  assert public.open_private_chat(member,member_chat,'es',null);
  assert public.save_chat_change(member,member_chat,0,null,'scan','foto','ok','{"materials":["glass","metal"]}');
  assert not public.save_chat_change(member,member_chat,0,null,'scan','foto','ok',null);
end $$;
set local role authenticated;
select set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000001","is_anonymous":true}',true);
do $$ begin
  assert (select count(*) = 1 from public.chat_turns);
  assert (select count(*) = 0 from public.activity_events);
  assert (select count(*) = 0 from public.profiles);
  assert not has_function_privilege(current_user,'public.save_chat_change(uuid,uuid,bigint,jsonb,text,text,text,jsonb,uuid)','EXECUTE');
  assert not has_function_privilege(current_user,'public.open_private_chat(uuid,uuid,text,jsonb)','EXECUTE');
end $$;
select set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000002","is_anonymous":true}',true);
do $$ begin
  assert (select count(*) = 0 from public.chat_turns);
  assert (select count(*) = 0 from public.conversations);
  delete from public.conversations;
  assert not found;
end $$;
select set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000003","is_anonymous":false}',true);
do $$ begin
  assert (select count(*) = 1 from public.activity_events);
  assert (select count(*) = 1 from public.profiles);
  assert (select materials = array['glass','metal'] from public.activity_events);
  assert not has_column_privilege(current_user,'public.profiles','id','UPDATE');
  assert not has_column_privilege(current_user,'public.conversations','owner_id','INSERT');
  insert into storage.objects(bucket_id,name) values ('avatars','00000000-0000-4000-8000-000000000003/avatar.jpg');
end $$;
select set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000004","is_anonymous":false}',true);
do $$ begin
  assert (select count(*) = 0 from public.activity_events);
  assert (select count(*) = 0 from public.conversations);
  assert (select count(*) = 0 from storage.objects where bucket_id = 'avatars');
  delete from public.activity_events;
  assert not found;
end $$;
select set_config('request.jwt.claims','{"sub":"00000000-0000-4000-8000-000000000003","is_anonymous":false}',true);
delete from public.conversations;
do $$ begin
  assert (select count(*) = 0 from public.chat_turns);
  assert (select count(*) = 1 from public.activity_events);
  assert not has_table_privilege('anon','public.activity_events','SELECT');
  assert not has_table_privilege('anon','public.chat_turns','SELECT');
end $$;
set local role service_role;
do $$ begin
  assert not public.save_chat_change('00000000-0000-4000-8000-000000000003','10000000-0000-4000-8000-000000000003',1,null,'late','late','late',null);
end $$;
reset role;
do $$ begin
  assert (select count(*) = 0 from public.activity_events where owner_id='00000000-0000-4000-8000-000000000001');
end $$;
rollback;
