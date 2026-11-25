-- Only FastAPI may create chats or certify assistant turns. Its caller ID comes
-- from Supabase Auth. Reads and deletes still use the end user's JWT and RLS.
create function public.open_private_chat(p_owner uuid, chat_id uuid, lang text, initial_material jsonb)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
  insert into public.conversations(id,owner_id,language,material)
  values (chat_id,p_owner,lang,initial_material) on conflict (id) do nothing;
  return exists(select 1 from public.conversations where id = chat_id and owner_id = p_owner);
end $$;
revoke all on function public.open_private_chat(uuid,uuid,text,jsonb) from public, anon, authenticated;
grant execute on function public.open_private_chat(uuid,uuid,text,jsonb) to service_role;

-- Commit an entire turn and its optional account activity in one transaction.
create function public.save_chat_change(p_owner uuid, chat_id uuid, expected bigint, new_material jsonb,
  p_prompt text, p_question text, p_answer text, p_activity jsonb, p_request uuid default null)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
  update public.conversations set revision = revision + 1, updated_at = now(),
    material = coalesce(new_material, material),
    title = case when title = '' and p_prompt is not null
                 then left(coalesce(nullif(p_question,''),'Imagen'),65) else title end
  where id = chat_id and owner_id = p_owner and revision = expected;
  if not found then return false; end if;
  if p_prompt is not null then
    insert into public.chat_turns(conversation_id,revision,request_id,prompt,question,answer)
    values (chat_id,expected+1,p_request,p_prompt,p_question,p_answer);
    if p_activity is not null and exists (select 1 from auth.users where id = p_owner and is_anonymous is false) then
      insert into public.activity_events(owner_id,materials,confidence,recyclable)
      values (p_owner,array(select jsonb_array_elements_text(p_activity->'materials')),
              (p_activity->>'confidence')::real, (p_activity->>'recyclable')::boolean);
    end if;
  end if;
  return true;
end $$;
revoke all on function public.save_chat_change(uuid,uuid,bigint,jsonb,text,text,text,jsonb,uuid) from public, anon, authenticated;
grant execute on function public.save_chat_change(uuid,uuid,bigint,jsonb,text,text,text,jsonb,uuid) to service_role;
