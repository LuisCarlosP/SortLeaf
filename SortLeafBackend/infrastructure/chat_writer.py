"""Narrow server-only interface for minting a chat and committing a model result."""
from postgrest import SyncPostgrestClient
from fastapi import HTTPException

from config.settings import SUPABASE_SERVICE_KEY, SUPABASE_URL


def _rpc(name, owner, fields):
    if not SUPABASE_SERVICE_KEY:
        raise HTTPException(503, "chat_storage_unavailable")
    headers = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    with SyncPostgrestClient(SUPABASE_URL.rstrip("/") + "/rest/v1", headers=headers, timeout=12) as client:
        return client.rpc(name, {"p_owner": owner, **fields}).execute().data


def open_chat(owner, chat_id, language, material):
    return _rpc("open_private_chat", owner, {"chat_id": chat_id, "lang": language, "initial_material": material})


def save_change(owner, fields):
    return _rpc("save_chat_change", owner, fields)
