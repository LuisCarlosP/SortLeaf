from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gotrue.errors import AuthApiError
import pytest


SIGNUP = {"email": "reader@example.org", "name": "Test", "last_name": "User", "password": "Password1234"}


def test_production_without_override_uses_sortleaf_recovery_url(client, monkeypatch):
    from services import supabase_auth_service
    monkeypatch.setattr(supabase_auth_service, "ENVIRONMENT", "production")
    monkeypatch.setattr(supabase_auth_service, "_FRONTEND_URL", "")
    with patch("core.identity.user_client") as factory:
        result = client.post("/auth/forgot-password", json={"email": SIGNUP["email"]})
        assert result.status_code == 200
        factory.return_value.auth.reset_password_email.assert_called_once_with(
            SIGNUP["email"], options={"redirect_to": "https://sortleaf.vercel.app/auth/reset-password"}
        )


def test_production_with_insecure_frontend_holds_email_requests(client, monkeypatch):
    from services import supabase_auth_service
    monkeypatch.setattr(supabase_auth_service, "ENVIRONMENT", "production")
    monkeypatch.setattr(supabase_auth_service, "_FRONTEND_URL", "http://localhost:5173")
    with patch("core.identity.user_client") as factory:
        for path, payload in [
            ("/auth/register", SIGNUP),
            ("/auth/resend-verification", {"email": SIGNUP["email"]}),
            ("/auth/forgot-password", {"email": SIGNUP["email"]}),
        ]:
            assert client.post(path, json=payload).status_code == 503
        auth = factory.return_value.auth
        auth.sign_up.assert_not_called()
        auth.resend.assert_not_called()
        auth.reset_password_email.assert_not_called()


def test_signup_waits_for_confirmation_and_resend_uses_the_same_callback(client):
    with patch("core.identity.user_client") as factory:
        db = factory.return_value
        db.auth.sign_up.return_value = SimpleNamespace(user=object(), session=None)
        signup = client.post("/auth/register", json=SIGNUP)
        resend = client.post("/auth/resend-verification", json={"email": SIGNUP["email"]})
        assert signup.status_code == 202 and resend.status_code == 200
        assert signup.json() == resend.json() == {"success": True, "message": "verification_pending"}
        redirect = db.auth.sign_up.call_args.args[0]["options"]["email_redirect_to"]
        assert redirect.endswith("/auth/callback")
        assert db.auth.resend.call_args.args[0] == {
            "type": "signup", "email": SIGNUP["email"], "options": {"email_redirect_to": redirect},
        }
        db.table.assert_not_called()


def test_registration_never_exposes_a_session_if_autoconfirm_is_misconfigured(client):
    with patch("core.identity.user_client") as factory:
        factory.return_value.auth.sign_up.return_value = SimpleNamespace(user=object(), session=object())
        response = client.post("/auth/register", json=SIGNUP)
    assert response.status_code == 503
    assert "tokens" not in response.json()


@pytest.mark.parametrize("upstream,status,expected", [
    ("user_not_found", 404, 200), ("over_email_send_rate_limit", 429, 429),
    ("unexpected_failure", 500, 503),
])
def test_resend_handles_delivery_problems_without_disclosing_accounts(client, upstream, status, expected):
    with patch("core.identity.user_client") as factory:
        factory.return_value.auth.resend.side_effect = AuthApiError("provider", status, upstream)
        response = client.post("/auth/resend-verification", json={"email": SIGNUP["email"]})
    assert response.status_code == expected


def test_unconfirmed_account_cannot_log_in_or_access_activity(client):
    pending = SimpleNamespace(id="pending", email=SIGNUP["email"], is_anonymous=False, email_confirmed_at=None)
    with patch("core.identity.user_client") as factory:
        db = factory.return_value
        db.auth.sign_in_with_password.return_value = SimpleNamespace(session=MagicMock(), user=pending)
        db.auth.get_user.return_value = SimpleNamespace(user=pending)
        login = client.post("/auth/login", json={"email": SIGNUP["email"], "password": SIGNUP["password"]})
        activity = client.get("/activity", headers={"Authorization": "Bearer pending"})
    assert login.status_code == 401 and login.json()["message"] == "email_not_confirmed"
    assert activity.status_code == 403
