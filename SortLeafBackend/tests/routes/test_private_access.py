from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from core.identity import Identity, get_identity, require_account
from fastapi import HTTPException


@pytest.mark.parametrize("method,path", [
    ("get","/activity"), ("delete","/activity"), ("get","/chat/conversations"),
    ("get","/chat/history/unknown"), ("post","/chat/session"), ("post","/chat/turn"),
    ("delete","/chat/session/unknown"), ("put","/chat/material"), ("post","/chat/message"),
])
def test_private_endpoints_require_identity(client, method, path):
    assert client.request(method, path).status_code == 401


def test_guest_bootstrap_issues_separate_tokens_without_a_login(client):
    token = lambda n: SimpleNamespace(access_token=f"access-{n}",refresh_token=f"refresh-{n}",
                                      expires_in=3600,expires_at=2000000000)
    with patch("core.identity.user_client") as sdk:
        sdk.return_value.auth.sign_in_anonymously.side_effect = [
            SimpleNamespace(session=token(1)), SimpleNamespace(session=token(2))]
        first, second = client.post("/auth/guest"), client.post("/auth/guest")
    assert first.status_code == second.status_code == 200
    assert first.json()["access_token"] != second.json()["access_token"]
    assert "no-store" in first.headers["cache-control"]


def test_guest_cannot_read_or_clear_account_activity(client):
    from app import app
    app.dependency_overrides[get_identity] = lambda: Identity("guest", True, MagicMock())
    assert client.get("/activity").status_code == 403
    assert client.delete("/activity").status_code == 403


def test_verified_identity_uses_each_callers_token():
    with patch("core.identity.user_client") as factory:
        def scoped(token):
            sdk = MagicMock()
            sdk.auth.get_user.return_value = SimpleNamespace(
                user=SimpleNamespace(id=token, email=None if token=="guest" else "a@example.test",
                                     is_anonymous=token=="guest", email_confirmed_at=None if token=="guest" else "2026-11-23"))
            return sdk
        factory.side_effect = scoped
        guest = next(get_identity("Bearer guest"))
        member = next(get_identity("Bearer member"))
    guest.db.auth.get_user.assert_called_once_with("guest")
    member.db.auth.get_user.assert_called_once_with("member")
    assert require_account(member).id == "member"
    with pytest.raises(HTTPException) as exc:
        require_account(guest)
    assert exc.value.status_code == 403
