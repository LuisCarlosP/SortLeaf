from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from core.identity import Identity
from services.private_chat import PrivateChatService


def test_commit_uses_verified_owner_and_restores_only_this_chats_context():
    provider = MagicMock()
    provider.send_message.return_value = "Respuesta privada"
    svc = PrivateChatService(Identity("account", False, MagicMock()), lambda: provider)
    session = {"id": "chat", "language": "es", "revision": 3, "material": None}
    previous = [{"prompt":"foto anterior","answer":"vidrio"}]
    with patch.object(svc, "_session", return_value=session), patch.object(svc, "_turns", return_value=previous), \
            patch("services.private_chat.save_change", return_value=True) as writer:
        answer = svc.send_turn("chat", "pregunta", "pregunta")
    provider.start_chat.return_value.restore_turn.assert_called_once_with("foto anterior", "vidrio")
    owner, payload = writer.call_args.args
    assert owner == "account"
    assert payload["chat_id"] == "chat" and payload["expected"] == 3
    assert answer["response"] == payload["p_answer"] == "Respuesta privada"


def test_local_decision_and_retry_never_invoke_the_model():
    factory = MagicMock()
    svc = PrivateChatService(Identity("guest", True, MagicMock()), factory)
    session = {"id":"chat","revision":1,"language":"es"}
    with patch.object(svc, "_session", return_value=session), patch("services.private_chat.save_change", return_value=True):
        svc.send_turn("chat","photo","",local_answer="Acerca el objeto.")
    with patch.object(svc, "_session", return_value=session):
        query = svc.db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value
        query.execute.return_value.data = [{"answer":"Acerca el objeto."}]
        assert svc.send_turn("chat","photo","",request_id="retry")["response"] == "Acerca el objeto."
    factory.assert_not_called()


def test_failed_commit_never_returns_an_unsaved_assistant_reply():
    svc = PrivateChatService(Identity("guest", True, MagicMock()), MagicMock())
    with patch("services.private_chat.save_change", return_value=False), pytest.raises(HTTPException) as exc:
        svc._commit({"id":"chat","revision":0,"language":"en"}, prompt="message", question="question", answer="answer")
    assert exc.value.status_code == 409
