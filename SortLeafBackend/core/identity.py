"""Request-scoped Supabase access. Postgres receives the caller's JWT for RLS."""
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException
from gotrue.errors import AuthApiError
from supabase import Client, ClientOptions, create_client

from config.settings import SUPABASE_KEY, SUPABASE_URL


def user_client(token: str | None = None) -> Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(
        headers=headers, auto_refresh_token=False, persist_session=False,
    ))


@contextmanager
def scoped_client(token=None):
    client = user_client(token)
    try:
        yield client
    finally:
        client.auth.close()
        # The SDK constructs Storage and PostgREST lazily.
        for name in ("_storage", "_postgrest"):
            transport = getattr(client, name, None)
            if transport is not None:
                transport.aclose()


@dataclass
class Identity:
    id: str
    guest: bool
    db: Client


def get_identity(authorization: str | None = Header(None)) -> Iterator[Identity]:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "session_required")
    with scoped_client(token) as db:
        yield verify_identity(token, db)


def verify_identity(token, db) -> Identity:
    try:
        result = db.auth.get_user(token)
    except AuthApiError as exc:
        raise HTTPException(401, "invalid_session") from exc
    except Exception as exc:
        raise HTTPException(503, "identity_unavailable") from exc
    if not result or not result.user:
        raise HTTPException(401, "invalid_session")
    user = result.user
    guest = user.is_anonymous is True or not user.email
    if not guest and not user.email_confirmed_at:
        raise HTTPException(403, "email_not_confirmed")
    return Identity(str(user.id), guest, db)


def require_account(identity: Identity = Depends(get_identity)) -> Identity:
    if identity.guest:
        raise HTTPException(403, "account_required")
    return identity
