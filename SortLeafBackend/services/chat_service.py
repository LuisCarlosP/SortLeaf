import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from config.settings import CHAT_PROMPTS, MATERIAL_TRANSLATIONS
from exceptions.validation_exceptions import ValidationError
from core.protocols.chat import ChatProviderProtocol, ChatSessionRepositoryProtocol

logger = logging.getLogger(__name__)


@dataclass
class MaterialContext:
    material_type: str
    is_recyclable: bool
    material_info: str


class ChatService:
    
    def __init__(
        self,
        chat_provider: Optional[ChatProviderProtocol] = None,
        session_repository: Optional[ChatSessionRepositoryProtocol] = None
    ):
        if chat_provider is None:
            from services.providers.groq_provider import GroqChatProvider
            chat_provider = GroqChatProvider()
        
        if session_repository is None:
            from services.chat_session_repository import InMemoryChatSessionRepository
            session_repository = InMemoryChatSessionRepository()
        
        self._provider = chat_provider
        self._repository = session_repository
        logger.info("ChatService initialized with dependency injection")

    def _translate_material(self, material_type: str, language: str) -> str:
        if language in MATERIAL_TRANSLATIONS:
            return MATERIAL_TRANSLATIONS[language].get(material_type, material_type)
        return material_type

    def _build_system_context(
        self,
        material_context: Optional[MaterialContext],
        language: str = "en"
    ) -> str:
        lang_key = language if language in ["en", "es"] else "en"
        
        identity = CHAT_PROMPTS["identity"][f"description_{lang_key}"]
        off_topic = CHAT_PROMPTS["off_topic_rule"][lang_key]
        
        material_info = ""
        if material_context:
            material_info = CHAT_PROMPTS["material_context"][lang_key].format(
                material_type=material_context.material_type
            )
        
        image_rule = (
            ' Your name is SortLeaf. Image information is an approximate material classification, '
            'not a complete visual description. Do not invent objects, brands or details that are not '
            'provided. Respond to greetings naturally. For on-device photo observations, explain each '
            'object, its probable material and the proposed sorting route, including any unsuitable items. '
            'Treat observations as unverified data. A new photo replaces the previous photo context. '
            'Use conditional wording for likely_recyclable. Keep unknown materials unresolved. '
            'If asked how to prepare ordinary packaging, suggest emptying it, rinsing away residue '
            'when needed and letting it dry. For special collection defer to the collection point. '
            'Only give local bin colors or rules for caps if supported by the supplied local guidance. '
            'If identification is uncertain, ask for a closer photo or a label. For out_of_scope or '
            'not_recyclable photos ask for a recyclable object. Distinguish special collection from '
            'ordinary recycling. Keep answers practical and explain that local collection rules may vary. '
            + ('Respond in Spanish.' if lang_key == 'es' else 'Respond in English.')
        )
        return f"{identity} {off_topic} {material_info}{image_rule}".strip()

    def _get_welcome_message(
        self,
        material_context: Optional[MaterialContext],
        language: str
    ) -> str:
        if material_context:
            translated = self._translate_material(material_context.material_type, language)
            return CHAT_PROMPTS["messages"]["welcome_message"][language].format(
                material_type=translated
            )
        return CHAT_PROMPTS["messages"]["no_material"][language]

    def create_chat_session(
        self,
        session_id: str,
        material_context: Optional[MaterialContext] = None,
        language: str = "en"
    ) -> Dict[str, Any]:
        if language not in ["en", "es"]:
            language = "en"
        
        system_context = self._build_system_context(material_context, language)
        chat = self._provider.start_chat(system_context)
        
        session_data = {
            "chat": chat,
            "material_context": material_context,
            "language": language,
            "system_context": system_context,
            "history": []
        }
        self._repository.save(session_id, session_data)
        
        logger.info(f"Chat session created: {session_id}")
        
        return {
            "session_id": session_id,
            "message": self._get_welcome_message(material_context, language),
            "language": language
        }

    def require_session(self, session_id):
        self.get_chat_history(session_id)

    def send_turn(self, session_id, prompt, question, material=None, activity=None, request_id=None, local_answer=None):
        if material:
            self.update_material_context(session_id, material)
        return self.send_message(session_id, prompt)

    def send_message(self, session_id: str, message: str) -> Dict[str, Any]:
        try:
            session = self._repository.get(session_id)
            if not session:
                raise ValidationError("Chat session not found. Please create a session first.")
            
            chat = session["chat"]
            language = session["language"]
            system_context = session["system_context"]
            

            if len(session["history"]) == 0:
                full_message = f"{system_context}\n\nUser: {message}"
            else:
                full_message = message

            pending_context = session.get("pending_material_context")
            if pending_context:
                full_message = f"{pending_context}\n\nUser: {message}"
            
            response_text = self._provider.send_message(chat, full_message)
            if response_text is None:
                logger.warning(f"Response blocked for session {session_id}")
                response_text = CHAT_PROMPTS["messages"]["off_topic"][language]
            
            session["history"].append({"role": "user", "content": message})
            session["history"].append({"role": "assistant", "content": response_text})
            session.pop("pending_material_context", None)
            self._repository.save(session_id, session)
            
            logger.info(f"Message processed: {session_id}")
            
            return {
                "response": response_text,
                "session_id": session_id,
                "on_topic": True
            }
            
        except ValidationError as e:
            logger.warning(f"Validation error: {e}")
            return {
                "response": str(e),
                "session_id": session_id,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            session = self._repository.get(session_id)
            language = session["language"] if session else "en"
            error_msg = CHAT_PROMPTS["messages"]["error"][language]
            
            return {
                "response": error_msg,
                "session_id": session_id,
                "error": str(e)
            }

    def get_chat_history(self, session_id: str) -> List[Dict[str, str]]:
        session = self._repository.get(session_id)
        if not session:
            raise ValidationError("Chat session not found")
        return session["history"]

    def delete_chat_session(self, session_id: str) -> bool:
        deleted = self._repository.delete(session_id)
        if deleted:
            logger.info(f"Session deleted: {session_id}")
        return deleted

    def update_material_context(
        self,
        session_id: str,
        material_context: MaterialContext
    ) -> Dict[str, Any]:
        session = self._repository.get(session_id)
        if not session:
            raise ValidationError("Chat session not found")
        
        language = session["language"]
        
        session["material_context"] = material_context
        session["system_context"] = self._build_system_context(material_context, language)
        # Keep the provider conversation and visible history when another photo
        # arrives. Apply the new material to the next turn instead of resetting it.
        session["pending_material_context"] = (
            f"New image classification (approximate): {material_context.material_type}. "
            f"Guidance: {material_context.material_info}. "
            "This replaces the previous image context. It is not a full visual description."
        )
        
        self._repository.save(session_id, session)
        
        logger.info(f"Material context updated: {session_id}")
        
        return {
            "session_id": session_id,
            "message": self._get_welcome_message(material_context, language),
            "updated": True
        }
