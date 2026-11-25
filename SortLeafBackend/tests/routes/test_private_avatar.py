from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PIL import Image
from app import app
from routes.auth import get_auth_service
from services.supabase_auth_service import SupabaseAuthService


@pytest.fixture
def account(client):
    owner = uuid4()
    db = MagicMock()
    service = SupabaseAuthService(db)
    profile = SimpleNamespace(profile_picture=f"{owner}/avatar.png")
    user = SimpleNamespace(id=owner, profile=profile)
    app.dependency_overrides[get_auth_service] = lambda: service
    with patch.object(service, "get_current_user", return_value=user):
        yield service, db, user


def test_avatar_uses_private_session_and_cannot_be_cached(client, account):
    _, db, user = account
    db.storage.from_.return_value.download.return_value = b"image bytes"
    res = client.get("/auth/profile/picture", headers={"Authorization": "Bearer owner"})
    assert res.status_code == 200 and res.content == b"image bytes"
    assert "no-store" in res.headers["Cache-Control"] and res.headers["Vary"] == "Authorization"
    db.storage.from_.assert_called_once_with("avatars")
    db.storage.from_.return_value.download.assert_called_once_with(user.profile.profile_picture)


def test_avatar_requires_sign_in(client, account):
    _, db, _ = account
    assert client.get("/auth/profile/picture").status_code == 401
    db.storage.from_.assert_not_called()


def test_other_users_avatar_reference_is_not_served(client, account):
    _, db, user = account
    user.profile.profile_picture = f"{uuid4()}/avatar.png"
    res = client.get("/auth/profile/picture", headers={"Authorization": "Bearer owner"})
    assert res.status_code == 404
    db.storage.from_.assert_not_called()


def test_upload_writes_only_an_owned_object_path(client, account):
    service, db, user = account
    picture = BytesIO()
    Image.new("RGB", (2, 2)).save(picture, format="PNG")
    with patch.object(service, "update_profile") as update:
        res = client.post("/auth/profile/picture", headers={"Authorization": "Bearer owner"},
                          files={"file": ("photo.png", picture.getvalue(), "image/png")})
    path = f"{user.id}/avatar.png"
    assert res.status_code == 200 and res.json()["path"] == path
    update.assert_called_once_with(user.id, profile_picture=path)
    db.storage.from_.return_value.get_public_url.assert_not_called()
