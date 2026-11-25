from dataclasses import asdict
from uuid import UUID

from fastapi import HTTPException
from infrastructure.chat_writer import open_chat, save_change

from core.identity import Identity
from services.chat_service import ChatService, MaterialContext


class PrivateChatService(ChatService):
    """Postgres authorizes each operation, including requests from older chat clients."""

    def __init__(self, identity: Identity, get_provider):
        self._get_provider = get_provider
        self.db = identity.db
        self.owner = identity.id

    def _chats(self):
        return self.db.table("conversations")

    def _session(self, session_id):
        try:
            UUID(session_id)
        except (TypeError, ValueError):
            raise HTTPException(404, "session_expired")
        rows = self._chats().select("*").eq("owner_id", self.owner).eq("id", session_id).execute().data
        if not rows:
            raise HTTPException(404, "session_expired")
        return rows[0]

    def require_session(self, session_id):
        self._session(session_id)

    def _turns(self, session_id, limit):
        rows = (self.db.table("chat_turns").select("revision,prompt,question,answer")
                .eq("conversation_id", session_id).order("revision", desc=True).limit(limit).execute().data)
        return list(reversed(rows))

    def list_chats(self):
        return (self._chats().select("id,title,updated_at").eq("owner_id", self.owner)
                .neq("title", "").order("updated_at", desc=True).order("id").limit(20).execute().data)

    def create_chat_session(self, session_id, material_context=None, language="en"):
        language = "es" if language == "es" else "en"
        if not open_chat(self.owner, session_id, language, asdict(material_context) if material_context else None):
            raise HTTPException(404, "session_expired")
        return {"session_id": session_id, "message": self._get_welcome_message(material_context, language),
                "language": language}

    def _commit(self, session, material=None, prompt=None, question=None, answer=None, activity=None, request_id=None):
        if prompt and not question:
            question = "Foto analizada" if session["language"] == "es" else "Photo analysis"
        saved = save_change(self.owner, {
            "chat_id": session["id"], "expected": session["revision"],
            "new_material": asdict(material) if material else None,
            "p_request": request_id, "p_activity": activity, "p_prompt": prompt, "p_question": question, "p_answer": answer,
        })
        if not saved:
            raise HTTPException(409, "chat_changed")

    def send_turn(self, session_id, prompt, question, material=None, activity=None, request_id=None, local_answer=None):
        session = self._session(session_id)
        if request_id:
            rows = (self.db.table("chat_turns").select("answer").eq("conversation_id", session_id)
                    .eq("request_id", request_id).limit(1).execute().data)
            if rows:
                return {"session_id": session_id, "response": rows[0]["answer"], "on_topic": True}
        if local_answer:
            self._commit(session, material, prompt, question, local_answer, activity, request_id)
            return {"session_id": session_id, "response": local_answer, "on_topic": True}
        previous = MaterialContext(**session["material"]) if session["material"] else None
        system = self._build_system_context(material or previous, session["language"])
        provider = self._get_provider()
        chat = provider.start_chat(system)
        turns = self._turns(session_id, 12)
        for turn in turns:
            chat.restore_turn(turn["prompt"], turn["answer"])
        if material or (previous and not turns):
            context = material or previous
            prompt = f"Latest classification: {context.material_type}. {context.material_info}\n\n{prompt}"
        try:
            answer = provider.send_message(chat, prompt)
        except Exception as exc:
            raise HTTPException(503, "assistant_unavailable") from exc
        if not answer:
            raise HTTPException(503, "assistant_unavailable")
        self._commit(session, material, prompt, question, answer, activity, request_id)
        return {"session_id": session_id, "response": answer, "on_topic": True}

    def send_message(self, session_id, message):
        return self.send_turn(session_id, message, message)

    def get_chat_history(self, session_id):
        self._session(session_id)
        history = []
        for turn in self._turns(session_id, 30):
            history.extend([{"role": "user", "content": turn["question"]},
                            {"role": "assistant", "content": turn["answer"]}])
        return history

    def delete_chat_session(self, session_id):
        self._session(session_id)
        return bool(self._chats().delete().eq("id", session_id).eq("owner_id", self.owner).execute().data)

    def update_material_context(self, session_id, material_context):
        session = self._session(session_id)
        self._commit(session, material_context)
        return {"session_id": session_id, "updated": True,
                "message": self._get_welcome_message(material_context, session["language"])}
