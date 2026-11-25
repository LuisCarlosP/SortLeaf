"""Isolated HTTP contract tests; no TensorFlow, credentials or external requests.

Run: python -m pytest --confcutdir=tests/conversation tests/conversation -q
The classifier and LLM are dependency-injected fakes; file validation is real.
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from services.chat_service import ChatService
from services.chat_session_repository import InMemoryChatSessionRepository
from services.providers.groq_provider import GroqChatSession
from core.dependencies import get_chat_service
from routes.chat import router, limiter, get_turn_prediction_service
from config.settings import MAX_FILE_SIZE


class FakeProvider:
    def start_chat(self, system_context):
        return {"system": system_context, "turns": []}

    def send_message(self, chat, message):
        chat["turns"].append(message)
        return "Separa el material y consulta las normas locales."


class FakePrediction:
    async def predict_image(self, data, filename, language):
        return {"class": "plastic", "confidence": 94.5, "is_recyclable": True,
                "message": "Consulta la recogida local", "language": language}


@pytest.fixture
def context():
    repository = InMemoryChatSessionRepository()
    provider = FakeProvider()
    service = ChatService(provider, repository)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_chat_service] = lambda: service
    app.dependency_overrides[get_turn_prediction_service] = lambda: FakePrediction()
    previous = limiter.enabled
    limiter.enabled = False
    with TestClient(app) as client:
        yield client, service, repository, provider
    limiter.enabled = previous


def photo():
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(output, format="PNG")
    return {"file": ("test.png", output.getvalue(), "image/png")}


def test_text_then_image_preserves_conversation(context):
    client, service, repository, _ = context
    first = client.post("/chat/turn", data={"message": "¿Cómo reciclo?", "language": "es"})
    assert first.status_code == 200
    session = first.json()["session_id"]
    assert first.json()["prediction"] is None
    original_chat = repository.get(session)["chat"]
    second = client.post("/chat/turn", data={"message": "¿Y este material?", "session_id": session}, files=photo())
    assert second.status_code == 200
    assert second.json()["session_id"] == session
    assert second.json()["prediction"]["class"] == "plastic"
    assert repository.get(session)["chat"] is original_chat
    assert len(service.get_chat_history(session)) == 4
    assert "plastic" in original_chat["turns"][-1]
    assert "¿Y este material?" in original_chat["turns"][-1]


def test_photo_only_gets_a_question(context):
    client, _, repository, _ = context
    response = client.post("/chat/turn", data={"language": "en"}, files=photo())
    assert response.status_code == 200
    session = repository.get(response.json()["session_id"])
    assert "What material" in session["history"][0]["content"]


@pytest.mark.parametrize("data", [{}, {"message": "   "}, {"message": "x" * 1001}, {"message": "hi", "language": "fr"}])
def test_invalid_turn_is_rejected(context, data):
    assert context[0].post("/chat/turn", data=data).status_code == 422


def test_expired_session_is_explicit(context):
    response = context[0].post("/chat/turn", data={"message": "Hola", "session_id": "missing"})
    assert response.status_code == 404
    assert response.json()["detail"] == "session_expired"


def test_invalid_image_is_rejected(context):
    response = context[0].post("/chat/turn", files={"file": ("bad.png", b"not an image", "image/png")})
    assert response.status_code == 400


def test_oversized_image_is_rejected(context):
    response = context[0].post("/chat/turn", files={"file": ("large.png", b"x" * (MAX_FILE_SIZE + 1), "image/png")})
    assert response.status_code == 413


def test_provider_failure_is_not_a_successful_reply(context):
    client, _, _, provider = context
    with patch.object(provider, "send_message", side_effect=RuntimeError("test outage")):
        response = client.post("/chat/turn", data={"message": "Hola"})
    assert response.status_code == 503
    assert response.json()["detail"] == "assistant_unavailable"


def test_groq_failure_does_not_duplicate_retry_history():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("test outage")
    session = GroqChatSession(client, "test-model", "system")
    with pytest.raises(RuntimeError):
        session.send_message("hello")
    assert session._history == [{"role": "system", "content": "system"}]

def test_online_session_receives_only_scan_data_and_keeps_it_for_follow_up(context):
    client, _, repository, _ = context
    scan = {"outcome": "identified", "items": [
        {"object": "bottle", "material": "glass", "route": "likely_recyclable"},
        {"object": "battery", "material": "composite", "route": "special_collection"},
    ]}
    first = client.post("/chat/turn", data={"visual_context": json.dumps(scan), "language": "es"})
    assert first.status_code == 200
    session = first.json()["session_id"]
    assert first.json()["prediction"] is None
    second = client.post("/chat/turn", data={"message": "¿Dónde llevo la batería?", "session_id": session})
    assert second.status_code == 200
    turns = repository.get(session)["chat"]["turns"]
    assert all(word in turns[0] for word in ("bottle", "glass", "battery", "special_collection"))
    assert turns[1] == "¿Dónde llevo la batería?"


@pytest.mark.parametrize("scan", [
    {"outcome": "identified", "items": []},
    {"outcome": "identified", "items": [{"object": "instructions", "material": "glass", "route": "unknown"}]},
    {"outcome": "uncertain", "items": [], "raw": "arbitrary label text"},
    {"outcome": "uncertain", "items": [{"object": "bottle", "material": "unknown", "route": "unknown"}] * 9},
])
def test_untrusted_or_oversized_scan_is_rejected(context, scan):
    assert context[0].post("/chat/turn", data={"visual_context": json.dumps(scan)}).status_code == 422


def test_scan_and_upload_cannot_be_combined(context):
    response = context[0].post("/chat/turn", data={"visual_context": '{"outcome":"uncertain","items":[]}'}, files=photo())
    assert response.status_code == 422

